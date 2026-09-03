from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .agent import MODEL, Case, Finding, InvestigationAgent, build_cases
from .audit import AuditLog
from .engine import Reconciliation, reconcile
from .load import load_dataset
from .money import rupees
from .schema import Dataset


@dataclass
class RunResult:
    run_id: str
    dataset_name: str
    dataset: Dataset
    recon: Reconciliation
    findings: list[Finding] = field(default_factory=list)
    audit: AuditLog = None
    report: dict = field(default_factory=dict)
    agent_ran: bool = False
    agent_error: str | None = None


def _exception_breakdown(recon: Reconciliation, findings: list[Finding]) -> list[dict]:
    counter: Counter[str] = Counter()
    value: Counter[str] = Counter()
    source: dict[str, str] = {}
    for d in recon.discrepancies:
        if d.classification:
            counter[d.classification] += 1
            value[d.classification] += abs(d.delta_paise)
            source[d.classification] = "rules"
    for f in findings:
        counter[f.classification] += 1
        value[f.classification] += abs(f.delta_paise)
        source.setdefault(f.classification, "agent")
    return [
        {
            "category": cat,
            "count": counter[cat],
            "value_paise": value[cat],
            "value": rupees(value[cat]),
            "resolved_by": source.get(cat, "agent"),
        }
        for cat in sorted(counter, key=lambda c: -value[c])
    ]


def build_report(result: RunResult) -> dict:
    recon = result.recon
    t = recon.totals
    findings = result.findings
    unresolved = [f for f in findings if f.classification == "UNRESOLVED"]
    uninvestigated = (
        [] if result.agent_ran else [c.key for c in build_cases(recon)]
    )
    agent_ms = sum(f.duration_ms for f in findings)
    return {
        "run_id": result.run_id,
        "dataset": result.dataset_name,
        "match_rate": {
            "records_matched": t["records_matched"],
            "records_total": t["records_total"],
            "match_rate_records": t["match_rate_records"],
            "value_matched": rupees(t["value_matched_paise"]),
            "value_total": rupees(t["value_total_paise"]),
            "match_rate_value": t["match_rate_value"],
        },
        "throughput": {
            "deterministic_runtime_ms": round(recon.runtime_ms, 2),
            "records_per_second": (
                round(t["records_total"] / (recon.runtime_ms / 1000), 1)
                if recon.runtime_ms > 0
                else None
            ),
            "agent_runtime_ms": round(agent_ms, 2),
            "agent_cases": len(findings),
        },
        "exceptions": {
            "total": t["discrepancies_total"],
            "closed_by_rules": t["resolved_by_rules"],
            "investigated_by_agent": len(findings),
            "breakdown": _exception_breakdown(recon, findings),
        },
        "timing_differences": {
            "unsettled_records": t["unsettled_records"],
            "unsettled_value": rupees(t["unsettled_value_paise"]),
            "note": "Payments captured but not yet settled at the T+2 cutoff. Expected, not an exception.",
        },
        "clearing_account": {
            "actual": rupees(recon.clearing["actual_paise"]),
            "explained_by_timing": rupees(recon.clearing["expected_timing_paise"]),
            "unexplained": rupees(recon.clearing["unexplained_paise"]),
            "unexplained_paise": recon.clearing["unexplained_paise"],
        },
        "could_not_resolve": [
            {
                "case": f.case_key,
                "delta": rupees(f.delta_paise),
                "explanation": f.explanation,
                "error": f.error,
            }
            for f in unresolved
        ],
        "not_investigated": uninvestigated,
        "agent_ran": result.agent_ran,
        "agent_error": result.agent_error,
        "agent_tokens": {
            "input": sum(f.input_tokens for f in findings),
            "output": sum(f.output_tokens for f in findings),
        },
    }


def run(
    data_dir: Path,
    name: str,
    use_agent: bool = True,
    progress=None,
    model: str | None = None,
    min_interval: float | None = None,
) -> RunResult:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    audit = AuditLog(run_id=run_id)
    audit.record("run_started", dataset=name, data_dir=str(data_dir), agent_requested=use_agent)

    dataset = load_dataset(Path(data_dir), name)
    audit.record(
        "dataset_loaded",
        settlement_rows=len(dataset.settlement_rows),
        vouchers=len(dataset.vouchers),
        settlements=len(dataset.settlements),
    )

    recon = reconcile(dataset)
    for m in recon.order_matches:
        audit.record(
            "match_decision",
            order_id=m.order_id,
            entity_id=m.entity_id,
            entry_type=m.entry_type,
            matched=m.matched,
            settled=m.settled,
            settlement_amount=m.settlement_amount,
            books_amount=m.books_amount,
            reason=m.reason,
        )
    for d in recon.discrepancies:
        audit.record("exception_detected", **d.to_dict())
    audit.record("deterministic_pass_complete", runtime_ms=round(recon.runtime_ms, 2), **recon.totals)

    result = RunResult(
        run_id=run_id, dataset_name=name, dataset=dataset, recon=recon, audit=audit
    )

    cases = build_cases(recon)
    audit.record("investigation_queue_built", case_count=len(cases),
                 cases=[{"key": c.key, "family": c.family, "delta_paise": c.delta_paise} for c in cases])

    if use_agent and cases:
        try:
            agent = InvestigationAgent(
                dataset, recon, model=model or MODEL,
                **({} if min_interval is None else {"min_request_interval_s": min_interval}),
            )
        except RuntimeError as exc:
            result.agent_error = str(exc)
            audit.record("agent_unavailable", reason=str(exc))
        else:
            result.agent_ran = True
            for i, case in enumerate(cases, start=1):
                if progress:
                    progress(i, len(cases), case)
                audit.record("investigation_started", case_key=case.key, family=case.family,
                             delta_paise=case.delta_paise,
                             exception_ids=[d.exception_id for d in case.discrepancies])
                finding = agent.investigate(case)
                for tc in finding.tool_calls:
                    audit.record("tool_call", case_key=case.key, tool=tc.tool,
                                 arguments=tc.arguments, ok=tc.ok,
                                 result_summary=tc.result_summary,
                                 duration_ms=round(tc.duration_ms, 2))
                audit.record("finding_recorded", **finding.to_dict())
                result.findings.append(finding)

    result.report = build_report(result)
    audit.record("run_completed", **result.report["match_rate"])
    return result
