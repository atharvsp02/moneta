from __future__ import annotations

import copy
import json
import os
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .engine import Discrepancy, ExceptionClass, Reconciliation
from .money import rupees
from .schema import Dataset
from .tools import TOOL_DEFINITIONS, ToolContext, ToolError, ToolRegistry

MODEL = "gemini-2.5-flash"
MAX_TURNS = 12
MAX_OUTPUT_TOKENS = 8192
MIN_REQUEST_INTERVAL_S = 4.0
MAX_RATE_LIMIT_RETRIES = 5

TERMINAL_FINISH_REASONS = {
    "MALFORMED_FUNCTION_CALL": "The model emitted a function call that could not be parsed.",
    "MAX_TOKENS": "The model hit the output token limit before concluding.",
    "SAFETY": "The response was blocked by a safety filter.",
    "PROHIBITED_CONTENT": "The response was blocked as prohibited content.",
    "BLOCKLIST": "The response was blocked by a terminology blocklist.",
    "SPII": "The response was blocked as containing sensitive personal information.",
    "RECITATION": "The response was blocked as recitation.",
    "TOO_MANY_TOOL_CALLS": "The model exceeded the allowed number of tool calls.",
}

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
    },
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


def _strip_unsupported(schema: dict) -> dict:
    cleaned = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            node.pop("additionalProperties", None)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(cleaned)
    return cleaned


def build_function_declarations() -> list[types.FunctionDeclaration]:
    declarations = []
    for spec in TOOL_DEFINITIONS + [SUBMIT_FINDING]:
        declarations.append(
            types.FunctionDeclaration(
                name=spec["name"],
                description=spec["description"],
                parameters_json_schema=_strip_unsupported(spec["input_schema"]),
            )
        )
    return declarations


class RateLimiter:
    def __init__(self, min_interval_s: float = MIN_REQUEST_INTERVAL_S):
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self.total_waited_s = 0.0

    def wait(self) -> None:
        if self.min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last
        if self._last and elapsed < self.min_interval_s:
            delay = self.min_interval_s - elapsed
            time.sleep(delay)
            self.total_waited_s += delay
        self._last = time.monotonic()

    def backoff(self, attempt: int) -> float:
        delay = min(2.0**attempt + random.uniform(0, 1.0), 60.0)
        time.sleep(delay)
        self.total_waited_s += delay
        self._last = time.monotonic()
        return delay


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
    rate_limit_waits: int = 0
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
    return [Case(key=k, scope=s, discrepancies=v, family=f) for (k, s, f), v in grouped.items()]


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


def resolve_api_key() -> str | None:
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
        value = os.environ.get(var)
        if value:
            return value
    return None


class InvestigationAgent:
    def __init__(
        self,
        dataset: Dataset,
        recon: Reconciliation,
        model: str = MODEL,
        client: genai.Client | None = None,
        min_request_interval_s: float = MIN_REQUEST_INTERVAL_S,
    ):
        if client is None:
            api_key = resolve_api_key()
            if not api_key:
                raise RuntimeError(
                    "No Gemini API key found. Set GEMINI_API_KEY (or GOOGLE_API_KEY). "
                    "The deterministic engine runs without it; the investigation agent needs it."
                )
            client = genai.Client(api_key=api_key)
        self.client = client
        self.model = model
        self.registry = ToolRegistry(ToolContext(dataset, recon))
        self.tools = [types.Tool(function_declarations=build_function_declarations())]
        self.limiter = RateLimiter(min_request_interval_s)

    def _config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=self.tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.0,
        )

    def _generate(self, contents: list[types.Content], finding: Finding):
        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            self.limiter.wait()
            try:
                return self.client.models.generate_content(
                    model=self.model, contents=contents, config=self._config()
                )
            except genai_errors.ClientError as exc:
                if getattr(exc, "status", None) == "RESOURCE_EXHAUSTED" or "429" in str(exc):
                    finding.rate_limit_waits += 1
                    self.limiter.backoff(attempt)
                    continue
                raise
            except genai_errors.ServerError:
                self.limiter.backoff(attempt)
                continue
        raise genai_errors.APIError("rate limit retries exhausted", {})

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
        contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part.from_text(text=_case_brief(case))])
        ]

        for turn in range(MAX_TURNS):
            finding.turns = turn + 1
            try:
                response = self._generate(contents, finding)
            except genai_errors.APIError as exc:
                finding.error = f"{type(exc).__name__}: {exc}"
                finding.explanation = (
                    "Investigation could not complete because the model API was unavailable. "
                    "The deterministic delta above still stands and needs manual review."
                )
                break

            usage = response.usage_metadata
            if usage:
                finding.input_tokens += usage.prompt_token_count or 0
                finding.output_tokens += usage.response_token_count or 0

            if not response.candidates:
                finding.error = "empty_response"
                finding.explanation = "The model returned no candidates for this case."
                break

            candidate = response.candidates[0]
            finish = str(getattr(candidate.finish_reason, "name", candidate.finish_reason) or "")
            content = candidate.content
            parts = list(content.parts) if content and content.parts else []
            calls = [p.function_call for p in parts if p.function_call]

            if finish in TERMINAL_FINISH_REASONS and not calls:
                finding.error = f"finish_reason:{finish}"
                finding.explanation = (
                    f"{TERMINAL_FINISH_REASONS[finish]} Reported as unresolved rather than guessing."
                )
                break

            if content:
                contents.append(content)

            if not calls:
                finding.error = finding.error or "model_stopped_without_submitting_finding"
                text = "".join(p.text for p in parts if p.text).strip()
                finding.explanation = text or finding.explanation
                break

            responses = []
            submitted = False
            for call in calls:
                args = dict(call.args or {})
                if call.name == "submit_finding":
                    missing = [
                        k
                        for k in SUBMIT_FINDING["input_schema"]["required"]
                        if k not in args
                    ]
                    if missing:
                        responses.append(
                            types.Part.from_function_response(
                                name=call.name,
                                response={"error": f"missing required fields: {missing}"},
                            )
                        )
                        continue
                    finding.classification = args["classification"]
                    finding.confidence = args["confidence"]
                    finding.explanation = args["explanation"]
                    finding.evidence = list(args["evidence"])
                    finding.recommended_action = args["recommended_action"]
                    responses.append(
                        types.Part.from_function_response(
                            name=call.name, response={"status": "finding recorded"}
                        )
                    )
                    submitted = True
                    continue

                t0 = time.perf_counter()
                try:
                    out = self.registry.call(call.name, args)
                    ok, payload = True, out
                except ToolError as exc:
                    ok, payload = False, {"error": str(exc)}
                except Exception as exc:
                    ok, payload = False, {"error": f"{type(exc).__name__}: {exc}"}
                dt = (time.perf_counter() - t0) * 1000
                finding.tool_calls.append(
                    ToolCallRecord(call.name, args, ok, _summarize(payload), dt)
                )
                responses.append(
                    types.Part.from_function_response(name=call.name, response=payload)
                )

            contents.append(types.Content(role="user", parts=responses))
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
