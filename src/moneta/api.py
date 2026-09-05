"""HTTP surface over a reconciliation run, for the dashboard.

The deterministic pass is cheap (sub-millisecond on a few hundred records), so the
server reconciles once at startup and holds the result in memory. Agent findings are a
different matter — they cost API calls and take seconds each — so they are loaded from
whatever `moneta reconcile` last wrote to `out/`, and only re-run when someone explicitly
asks for it via POST /api/investigate.

Nothing here writes to the merchant's books. Every route is a read over data already
loaded, plus one route that asks the model a question. That is the whole surface, by
design.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import MODEL, QuestionAnswerer, resolve_api_key
from .engine import reconcile
from .eval import evaluate
from .load import load_dataset
from .money import rupees
from .pipeline import RunResult, build_report, run
from .audit import AuditLog

DATA_DIR = Path(os.environ.get("MONETA_DATA_DIR", "data"))
OUT_DIR = Path(os.environ.get("MONETA_OUT_DIR", "out"))
DATASET = os.environ.get("MONETA_DATASET", "dev")
ALLOWED_ORIGINS = os.environ.get(
    "MONETA_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list, max_length=20)


class State:
    """Everything the server serves. Rebuilt only on explicit reload."""

    def __init__(self) -> None:
        self.dataset_name = DATASET
        self.reload()

    def reload(self, name: str | None = None) -> None:
        self.dataset_name = name or self.dataset_name
        self.dataset = load_dataset(DATA_DIR, self.dataset_name)
        self.recon = reconcile(self.dataset)
        self.findings = _load_findings(OUT_DIR / f"{self.dataset_name}.findings.json")
        self.audit = _load_audit(OUT_DIR / f"{self.dataset_name}.audit.jsonl")
        self.report = _load_report(OUT_DIR / f"{self.dataset_name}.report.json")
        self._qa: QuestionAnswerer | None = None

    @property
    def qa(self) -> QuestionAnswerer:
        if self._qa is None:
            self._qa = QuestionAnswerer(self.dataset, self.recon, self.findings)
        return self._qa


def _load_findings(path: Path) -> list:
    """Rehydrate findings written by the CLI.

    The Q&A tools read a handful of attributes off each finding, so a namespace with
    those fields is enough and avoids reconstructing the nested tool-call records.
    """
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [
        SimpleNamespace(
            case_key=f.get("case_key", ""),
            scope=f.get("scope", ""),
            family=f.get("family", ""),
            exception_ids=f.get("exception_ids", []),
            delta_paise=f.get("delta_paise", 0),
            delta=f.get("delta", ""),
            classification=f.get("classification", "UNRESOLVED"),
            confidence=f.get("confidence", "low"),
            explanation=f.get("explanation", ""),
            evidence=f.get("evidence", []),
            recommended_action=f.get("recommended_action", ""),
            tool_calls=f.get("tool_calls", []),
            turns=f.get("turns", 0),
            duration_ms=f.get("duration_ms", 0.0),
            input_tokens=f.get("input_tokens", 0),
            output_tokens=f.get("output_tokens", 0),
            error=f.get("error"),
            investigated_at=f.get("investigated_at", ""),
        )
        for f in raw
    ]


def _load_audit(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _load_report(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _finding_dict(f) -> dict:
    return {
        "case_key": f.case_key,
        "scope": f.scope,
        "family": f.family,
        "exception_ids": list(f.exception_ids),
        "delta_paise": f.delta_paise,
        "delta": rupees(f.delta_paise),
        "classification": f.classification,
        "confidence": f.confidence,
        "explanation": f.explanation,
        "evidence": list(f.evidence),
        "recommended_action": f.recommended_action,
        "tool_calls": [
            tc if isinstance(tc, dict) else {"tool": tc.tool, "arguments": tc.arguments}
            for tc in f.tool_calls
        ],
        "turns": f.turns,
        "duration_ms": f.duration_ms,
        "error": f.error,
        "investigated_at": f.investigated_at,
    }


app = FastAPI(
    title="Moneta",
    version="0.1.0",
    description="Settlement intelligence agent. Read-only: no route modifies the ledger.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

state = State()


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "dataset": state.dataset_name,
        "settlement_rows": len(state.dataset.settlement_rows),
        "vouchers": len(state.dataset.vouchers),
        "settlement_cycles": len(state.dataset.settlements),
        "findings_loaded": len(state.findings),
        "audit_events": len(state.audit),
        "agent_available": bool(resolve_api_key()),
        "model": MODEL,
    }


@app.get("/api/summary")
def summary() -> dict:
    t = state.recon.totals
    c = state.recon.clearing
    # Each exception is counted exactly once, under whichever layer actually resolved
    # it. An exception the agent has investigated is no longer awaiting investigation,
    # so attributing it to both would inflate the totals past the number of exceptions
    # that exist.
    finding_for: dict[str, object] = {}
    for f in state.findings:
        for eid in f.exception_ids:
            finding_for[eid] = f

    breakdown: dict[str, dict] = {}
    closed_by_rules = attributed_by_agent = unresolved = awaiting = 0

    for d in state.recon.discrepancies:
        finding = finding_for.get(d.exception_id)
        if d.classification:
            cls, resolved_by = d.classification, "rules"
            closed_by_rules += 1
        elif finding is not None:
            cls, resolved_by = finding.classification, "agent"
            if cls == "UNRESOLVED":
                unresolved += 1
            else:
                attributed_by_agent += 1
        else:
            cls, resolved_by = "OPEN_FOR_INVESTIGATION", "pending"
            awaiting += 1

        row = breakdown.setdefault(
            cls, {"category": cls, "count": 0, "value_paise": 0, "resolved_by": resolved_by}
        )
        row["count"] += 1
        row["value_paise"] += abs(d.delta_paise)

    for row in breakdown.values():
        row["value"] = rupees(row["value_paise"])

    return {
        "dataset": state.dataset_name,
        "match_rate": {
            "records_matched": t["records_matched"],
            "records_total": t["records_total"],
            "match_rate_records": t["match_rate_records"],
            "value_matched": rupees(t["value_matched_paise"]),
            "value_matched_paise": t["value_matched_paise"],
            "value_total": rupees(t["value_total_paise"]),
            "value_total_paise": t["value_total_paise"],
            "value_unresolved": rupees(t["value_total_paise"] - t["value_matched_paise"]),
            "match_rate_value": t["match_rate_value"],
        },
        # These four are a partition of `total`, so the dashboard can show them as one bar.
        "exceptions": {
            "total": t["discrepancies_total"],
            "closed_by_rules": closed_by_rules,
            "attributed_by_agent": attributed_by_agent,
            "unresolved": unresolved,
            "open_for_agent": awaiting,
            "investigated": attributed_by_agent + unresolved,
            "breakdown": sorted(breakdown.values(), key=lambda r: -r["value_paise"]),
        },
        "timing": {
            "unsettled_records": t["unsettled_records"],
            "unsettled_value": rupees(t["unsettled_value_paise"]),
            "note": "Captured but not yet settled at the T+2 cutoff. Expected, not an exception.",
        },
        "clearing_account": {
            "actual": rupees(c["actual_paise"]),
            "explained_by_timing": rupees(c["expected_timing_paise"]),
            "unexplained": rupees(c["unexplained_paise"]),
            "unexplained_paise": c["unexplained_paise"],
        },
        "throughput": {
            "deterministic_runtime_ms": round(state.recon.runtime_ms, 3),
            "records_per_second": (
                round(t["records_total"] / (state.recon.runtime_ms / 1000))
                if state.recon.runtime_ms > 0
                else None
            ),
        },
        "agent_available": bool(resolve_api_key()),
    }


@app.get("/api/report")
def report() -> dict:
    if state.report:
        return state.report
    stub = RunResult(
        run_id="live",
        dataset_name=state.dataset_name,
        dataset=state.dataset,
        recon=state.recon,
        findings=[],
        audit=AuditLog(run_id="live"),
    )
    return build_report(stub)


@app.get("/api/orders")
def orders(
    status: str = Query("all", pattern="^(all|matched|exception|unsettled)$"),
    q: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict:
    by_key: dict[str, list] = {}
    for d in state.recon.discrepancies:
        by_key.setdefault(d.key, []).append(d)

    rows = []
    for m in state.recon.order_matches:
        related = by_key.get(m.order_id, [])
        rows.append(
            {
                "order_id": m.order_id,
                "entity_id": m.entity_id,
                "entry_type": m.entry_type,
                "matched": m.matched,
                "settled": m.settled,
                "settlement_amount": rupees(m.settlement_amount),
                "settlement_amount_paise": m.settlement_amount,
                "books_amount": rupees(m.books_amount) if m.books_amount is not None else None,
                "books_amount_paise": m.books_amount,
                "delta": (
                    rupees(m.books_amount - m.settlement_amount)
                    if m.books_amount is not None
                    else None
                ),
                "reason": m.reason,
                "classification": next(
                    (d.classification for d in related if d.classification), None
                ),
                "exception_ids": [d.exception_id for d in related],
            }
        )

    if status == "matched":
        rows = [r for r in rows if r["matched"]]
    elif status == "exception":
        rows = [r for r in rows if not r["matched"]]
    elif status == "unsettled":
        rows = [r for r in rows if not r["settled"]]
    if q:
        needle = q.lower().strip()
        rows = [
            r
            for r in rows
            if needle in r["order_id"].lower()
            or needle in r["entity_id"].lower()
            or needle in (r["classification"] or "").lower()
        ]

    return {
        "total": len(rows),
        "returned": len(rows[offset : offset + limit]),
        "offset": offset,
        "orders": rows[offset : offset + limit],
    }


@app.get("/api/exceptions")
def exceptions(classification: str | None = None) -> dict:
    findings_by_key: dict[str, list] = {}
    for f in state.findings:
        findings_by_key.setdefault(f.case_key, []).append(f)

    rows = []
    for d in state.recon.discrepancies:
        payload = d.to_dict()
        matched = findings_by_key.get(d.key, [])
        agent = next((f for f in matched if d.exception_id in f.exception_ids), None)
        payload["status"] = (
            "closed_by_rules"
            if d.classification
            else ("investigated" if agent else "open_for_agent")
        )
        if agent:
            payload["classification"] = agent.classification
            payload["confidence"] = agent.confidence
            payload["agent_finding"] = _finding_dict(agent)
        rows.append(payload)

    if classification:
        rows = [r for r in rows if (r["classification"] or "OPEN") == classification]
    rows.sort(key=lambda r: -abs(r["delta_paise"]))
    return {"total": len(rows), "exceptions": rows}


@app.get("/api/findings")
def findings() -> dict:
    rows = [_finding_dict(f) for f in state.findings]
    return {
        "total": len(rows),
        "unresolved": sum(1 for r in rows if r["classification"] == "UNRESOLVED"),
        "findings": rows,
    }


@app.get("/api/settlements")
def settlements() -> dict:
    rows = []
    for check in state.recon.settlement_checks:
        row = dict(check)
        for key in list(row):
            if key.endswith("_paise") and isinstance(row[key], int):
                row[key.removesuffix("_paise")] = rupees(row[key])
        rows.append(row)
    return {"total": len(rows), "settlements": rows}


@app.get("/api/audit")
def audit(
    event: str | None = None,
    limit: int = Query(500, ge=1, le=20000),
    offset: int = Query(0, ge=0),
) -> dict:
    events = state.audit
    if event:
        events = [e for e in events if e.get("event") == event]
    counts: dict[str, int] = {}
    for e in state.audit:
        counts[e.get("event", "?")] = counts.get(e.get("event", "?"), 0) + 1
    return {
        "total": len(events),
        "offset": offset,
        "counts": counts,
        "events": events[offset : offset + limit],
    }


@app.get("/api/eval")
def eval_report() -> dict:
    cached = OUT_DIR / f"{state.dataset_name}.eval.json"
    if cached.is_file():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    labels = DATA_DIR / f"{state.dataset_name}.labels.json"
    if not labels.is_file():
        raise HTTPException(404, f"no ground-truth labels for dataset '{state.dataset_name}'")
    stub = RunResult(
        run_id="live",
        dataset_name=state.dataset_name,
        dataset=state.dataset,
        recon=state.recon,
        findings=list(state.findings),
        audit=AuditLog(run_id="live"),
    )
    stub.report = build_report(stub)
    return evaluate(stub, DATA_DIR, state.dataset_name).report


@app.post("/api/ask")
def ask(body: AskRequest) -> dict:
    if not resolve_api_key():
        raise HTTPException(
            503,
            "No Gemini API key configured, so the Q&A layer is unavailable. The reconciliation "
            "numbers on this page are produced by the deterministic engine and are unaffected.",
        )
    try:
        answer = state.qa.ask(body.question, body.history)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "question": answer.question,
        "answer": answer.answer,
        "tool_calls": [
            {
                "tool": tc.tool,
                "arguments": tc.arguments,
                "ok": tc.ok,
                "result_summary": tc.result_summary,
                "duration_ms": round(tc.duration_ms, 2),
            }
            for tc in answer.tool_calls
        ],
        "turns": answer.turns,
        "duration_ms": round(answer.duration_ms, 1),
        "tokens": {"input": answer.input_tokens, "output": answer.output_tokens},
        "error": answer.error,
        "answered_at": answer.answered_at,
    }


@app.post("/api/investigate")
def investigate() -> dict:
    """Re-run the full pipeline with the agent enabled and adopt the fresh result."""
    if not resolve_api_key():
        raise HTTPException(503, "No Gemini API key configured; the investigation agent needs one.")
    result = run(DATA_DIR, state.dataset_name, use_agent=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result.audit.write_jsonl(OUT_DIR / f"{state.dataset_name}.audit.jsonl")
    (OUT_DIR / f"{state.dataset_name}.report.json").write_text(
        json.dumps(result.report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / f"{state.dataset_name}.findings.json").write_text(
        json.dumps([f.to_dict() for f in result.findings], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    state.reload()
    return {
        "run_id": result.run_id,
        "agent_ran": result.agent_ran,
        "agent_error": result.agent_error,
        "cases_investigated": len(result.findings),
        "unresolved": sum(1 for f in result.findings if f.classification == "UNRESOLVED"),
    }


@app.post("/api/reload")
def reload_state(name: str | None = None) -> dict:
    state.reload(name)
    return health()
