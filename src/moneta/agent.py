from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import anthropic

from .engine import Discrepancy, ExceptionClass, Reconciliation
from .money import rupees
from .schema import Dataset
from .tools import TOOL_DEFINITIONS, ToolContext, ToolError, ToolRegistry

MODEL = "claude-opus-5"
MAX_TURNS = 12
MAX_TOKENS = 16000

CLASSIFICATIONS = [
    ExceptionClass.AGGREGATE_FEE_MISMATCH.value,
    ExceptionClass.GST_INPUT_ROUNDING_DRIFT.value,
    ExceptionClass.CROSS_CYCLE_REFUND.value,
    ExceptionClass.MISSING_IN_BOOKS.value,
    ExceptionClass.MISSING_IN_SETTLEMENT.value,
    ExceptionClass.MISSING_REFUND_IN_BOOKS.value,
    ExceptionClass.DUPLICATE_BOOKING.value,
    ExceptionClass.AMOUNT_MISMATCH.value,
    ExceptionClass.SETTLEMENT_NET_MISMATCH.value,
    "UNRESOLVED",
]

SUBMIT_FINDING = {
    "name": "submit_finding",
    "description": (
        "Record your conclusion for this case. Call this exactly once, at the end, after you have "
        "gathered evidence with the other tools. If the evidence does not support a specific "
        "classification, submit UNRESOLVED rather than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "classification": {"type": "string", "enum": CLASSIFICATIONS},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "explanation": {
                "type": "string",
                "description": "Plain-language explanation a finance controller can act on. State the root cause and the amount.",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific facts retrieved via tools that support the conclusion. Cite ids and amounts.",
            },
            "recommended_action": {
                "type": "string",
                "description": "What a human should do. Moneta never edits the ledger itself.",
            },
        },
        "required": ["classification", "confidence", "explanation", "evidence", "recommended_action"],
        "additionalProperties": False,
    },
    "strict": True,
}

SYSTEM_PROMPT = """You are Moneta, a settlement reconciliation analyst for an Indian merchant using Razorpay.

A deterministic rules engine has already done all the matching. It reconstructs each settlement as:

    net bank credit = gross payments - MDR fee - GST on fee - refunds netted

The engine has found a discrepancy it can quantify but cannot attribute to a root cause. Your job is to investigate that specific case and explain it.

How you must work:

1. NEVER do arithmetic yourself. You are working with money. Every number must come from a tool result. `compute_fee_breakdown` exists precisely so that you never have to compute a fee, a blended rate, or a GST figure in your head. If you need a calculation that no tool provides, say so in your finding rather than estimating.

2. NEVER invent an explanation that fits. Reconciliation errors have specific causes and a plausible-sounding wrong answer is worse than an honest "unresolved", because someone will act on it. If the evidence does not identify the cause, submit UNRESOLVED and say exactly what you checked and what was missing.

3. Investigate before concluding. Pull the settlement, the entries, the merchant's vouchers, and where relevant search other cycles. A value that is missing from one cycle has usually gone somewhere findable.

4. You are read-only and bounded. You have no ability to modify the ledger or move money, by design. Your output is a finding for a human to review and act on.

Common causes worth testing:
- The merchant booked the gateway fee at an assumed flat rate, while Razorpay charges per-method MDR and UPI is zero-rated. `compute_fee_breakdown` shows the true blended rate.
- The merchant computed GST as 18% of the aggregate fee, while Razorpay rounds GST per transaction. This produces a drift of a few paise. The same tool reports both figures.
- A refund was recorded in the books on the date it was issued but netted by Razorpay out of a later settlement cycle. This shows up as two equal and opposite deltas in adjacent cycles. `fetch_refunds_for_order` and `search_settlement_entries_by_amount` will find it.

Finish by calling submit_finding exactly once."""


@dataclass
class ToolCallRecord:
    tool: str
    arguments: dict
    ok: bool
    result_summary: str
    duration_ms: float


@dataclass
class Finding:
    case_key: str
    scope: str
    family: str
    exception_ids: list[str]
    delta_paise: int
    classification: str
    confidence: str
    explanation: str
    evidence: list[str]
    recommended_action: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    investigated_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["delta"] = rupees(self.delta_paise)
        return d


@dataclass
class Case:
    key: str
    scope: str
    discrepancies: list[Discrepancy]
    family: str = "general"

    @property
    def delta_paise(self) -> int:
        return sum(d.delta_paise for d in self.discrepancies)


SYMPTOM_FAMILY = {
    "booked_gateway_fee_not_equal_to_sum_of_per_order_fees": "fee_and_gst",
    "booked_gst_input_credit_not_equal_to_sum_of_per_order_tax": "fee_and_gst",
    "booked_bank_credit_not_equal_to_reconstructed_settlement_net": "bank_credit",
}


def build_cases(recon: Reconciliation) -> list[Case]:
    grouped: dict[tuple[str, str, str], list[Discrepancy]] = {}
    for d in recon.open_investigations:
        family = SYMPTOM_FAMILY.get(d.rule, d.rule)
        grouped.setdefault((d.key, d.scope, family), []).append(d)
    return [
        Case(key=k, scope=s, discrepancies=v, family=f) for (k, s, f), v in grouped.items()
    ]


def _case_brief(case: Case) -> str:
    lines = [
        f"Case key: {case.key}",
        f"Scope: {case.scope}",
        f"Symptom family: {case.family}",
        f"Combined unexplained delta: {rupees(case.delta_paise)}",
        "",
        "The rules engine flagged the following and could not attribute a cause:",
    ]
    for d in case.discrepancies:
        lines.append(f"\n- [{d.exception_id}] rule: {d.rule}")
        lines.append(f"  delta: {rupees(d.delta_paise)} (books minus Razorpay)")
        for e in d.evidence:
            lines.append(f"  evidence ({e['source']}): {e['detail']} :: {json.dumps(e['data'])}")
    lines.append("\nInvestigate this case and submit exactly one finding.")
    return "\n".join(lines)


def _summarize(result: dict) -> str:
    text = json.dumps(result)
    return text if len(text) <= 240 else text[:237] + "..."


class InvestigationAgent:
    def __init__(self, dataset: Dataset, recon: Reconciliation, model: str = MODEL,
                 client: anthropic.Anthropic | None = None):
        if client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. The deterministic engine runs without it; "
                    "the investigation agent needs it."
                )
            client = anthropic.Anthropic(max_retries=3)
        self.client = client
        self.model = model
        self.registry = ToolRegistry(ToolContext(dataset, recon))
        self.tools = TOOL_DEFINITIONS + [SUBMIT_FINDING]

    def investigate(self, case: Case) -> Finding:
        started = time.perf_counter()
        finding = Finding(
            case_key=case.key,
            scope=case.scope,
            family=case.family,
            exception_ids=[d.exception_id for d in case.discrepancies],
            delta_paise=case.delta_paise,
            classification="UNRESOLVED",
            confidence="low",
            explanation="",
            evidence=[],
            recommended_action="",
            investigated_at=datetime.now(timezone.utc).isoformat(),
        )
        messages: list[dict] = [{"role": "user", "content": _case_brief(case)}]

        for turn in range(MAX_TURNS):
            finding.turns = turn + 1
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=self.tools,
                    thinking={"type": "adaptive"},
                )
            except anthropic.APIStatusError as exc:
                finding.error = f"{type(exc).__name__}: {exc.status_code}"
                finding.explanation = (
                    "Investigation could not complete because the model API returned an error. "
                    "The deterministic delta above still stands and needs manual review."
                )
                break
            except anthropic.APIConnectionError as exc:
                finding.error = f"APIConnectionError: {exc}"
                finding.explanation = (
                    "Investigation could not complete because the model API was unreachable. "
                    "The deterministic delta above still stands and needs manual review."
                )
                break

            finding.input_tokens += response.usage.input_tokens
            finding.output_tokens += response.usage.output_tokens

            if response.stop_reason == "refusal":
                finding.error = "model_refusal"
                finding.explanation = "The model declined to answer this case."
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                finding.error = finding.error or "model_stopped_without_submitting_finding"
                finding.explanation = next(
                    (b.text for b in response.content if b.type == "text"), finding.explanation
                )
                break

            results = []
            submitted = False
            for block in tool_uses:
                if block.name == "submit_finding":
                    payload = block.input
                    finding.classification = payload["classification"]
                    finding.confidence = payload["confidence"]
                    finding.explanation = payload["explanation"]
                    finding.evidence = payload["evidence"]
                    finding.recommended_action = payload["recommended_action"]
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": "Finding recorded."}
                    )
                    submitted = True
                    continue
                t0 = time.perf_counter()
                try:
                    out = self.registry.call(block.name, dict(block.input))
                    ok, content = True, json.dumps(out)
                except ToolError as exc:
                    ok, content = False, json.dumps({"error": str(exc)})
                except Exception as exc:
                    ok, content = False, json.dumps({"error": f"{type(exc).__name__}: {exc}"})
                dt = (time.perf_counter() - t0) * 1000
                finding.tool_calls.append(
                    ToolCallRecord(block.name, dict(block.input), ok, _summarize(json.loads(content)), dt)
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                        **({"is_error": True} if not ok else {}),
                    }
                )
            messages.append({"role": "user", "content": results})
            if submitted:
                break
        else:
            finding.error = "max_turns_exceeded"
            finding.explanation = (
                "Investigation hit the turn limit without reaching a conclusion. Reported as "
                "unresolved rather than guessing."
            )

        finding.duration_ms = (time.perf_counter() - started) * 1000
        return finding

    def investigate_all(self, cases: list[Case], progress=None) -> list[Finding]:
        findings = []
        for i, case in enumerate(cases, start=1):
            if progress:
                progress(i, len(cases), case)
            findings.append(self.investigate(case))
        return findings
