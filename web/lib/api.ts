/**
 * Typed client for the Moneta reconciliation API.
 *
 * Every route here is a read except `ask` and `investigate`, and neither of those
 * writes to the merchant's books — `ask` questions a finished run, `investigate`
 * re-runs the agent over cases the rules engine left open.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_MONETA_API ?? "http://127.0.0.1:8000"

export type Health = {
  status: string
  dataset: string
  settlement_rows: number
  vouchers: number
  settlement_cycles: number
  findings_loaded: number
  audit_events: number
  agent_available: boolean
  model: string
}

export type Summary = {
  dataset: string
  match_rate: {
    records_matched: number
    records_total: number
    match_rate_records: number
    value_matched: string
    value_matched_paise: number
    value_total: string
    value_total_paise: number
    value_unresolved: string
    match_rate_value: number
  }
  exceptions: {
    total: number
    closed_by_rules: number
    open_for_agent: number
    investigated: number
    unresolved: number
    breakdown: {
      category: string
      count: number
      value_paise: number
      value: string
      resolved_by: string
    }[]
  }
  timing: { unsettled_records: number; unsettled_value: string; note: string }
  clearing_account: {
    actual: string
    explained_by_timing: string
    unexplained: string
    unexplained_paise: number
  }
  throughput: {
    deterministic_runtime_ms: number
    records_per_second: number | null
  }
  agent_available: boolean
}

export type OrderRow = {
  order_id: string
  entity_id: string
  entry_type: string
  matched: boolean
  settled: boolean
  settlement_amount: string
  settlement_amount_paise: number
  books_amount: string | null
  books_amount_paise: number | null
  delta: string | null
  reason: string
  classification: string | null
  exception_ids: string[]
}

export type Evidence = { source: string; detail: string; data: Record<string, unknown> }

export type AgentFinding = {
  case_key: string
  scope: string
  family: string
  exception_ids: string[]
  delta_paise: number
  delta: string
  classification: string
  confidence: string
  explanation: string
  evidence: string[]
  recommended_action: string
  tool_calls: { tool: string; arguments: Record<string, unknown> }[]
  turns: number
  duration_ms: number
  error: string | null
  investigated_at: string
}

export type ExceptionRow = {
  exception_id: string
  scope: string
  key: string
  delta_paise: number
  delta: string
  rule: string
  detected_by: string
  classification: string | null
  confidence: number | string | null
  evidence: Evidence[]
  status: "closed_by_rules" | "open_for_agent" | "investigated"
  agent_finding?: AgentFinding
}

export type AuditEvent = {
  ts: string
  run_id: string
  event: string
  [key: string]: unknown
}

export type SettlementCheck = {
  settlement_id: string
  settlement_utr: string
  settled_at: string
  payment_count: number
  refund_count: number
  gross: string
  fee: string
  tax: string
  refund: string
  reconstructed_net: string
  reported_net: string
  booked: boolean
  booked_net?: string | null
  bank_delta_paise?: number
  bank_delta?: string
  fee_delta_paise?: number
  tax_delta_paise?: number
  [key: string]: unknown
}

export type EvalReport = {
  dataset: string
  run_id: string
  agent_ran: boolean
  agent_error: string | null
  totals: {
    injected_errors: number
    detected: number
    correctly_attributed: number
    misclassified: number
    missed: number
    honestly_unresolved: number
    unclaimed_predictions: number
    detection_recall: number | null
    attribution_accuracy: number | null
    micro_precision: number | null
    micro_recall: number | null
    macro_precision: number | null
    macro_recall: number | null
  }
  by_layer: Record<string, { attributed: number; correct: number; accuracy: number | null }>
  per_class: Record<
    string,
    {
      support: number
      true_positives: number
      false_positives: number
      false_negatives: number
      precision: number | null
      recall: number | null
      f1: number | null
    }
  >
  misses: { key: string; expected: string; expected_impact: string }[]
  misclassifications: { key: string; expected: string; predicted: string }[]
}

export type ToolCall = {
  tool: string
  arguments: Record<string, unknown>
  ok: boolean
  result_summary: string
  duration_ms: number
}

export type AskResponse = {
  question: string
  answer: string
  tool_calls: ToolCall[]
  turns: number
  duration_ms: number
  tokens: { input: number; output: number }
  error: string | null
  answered_at: string
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    })
  } catch {
    throw new ApiError(
      `Cannot reach the Moneta API at ${API_BASE}. Start it with: moneta serve`,
      0,
    )
  }
  if (!response.ok) {
    // FastAPI puts the human-readable reason in `detail`; surface that rather than
    // a bare status code, because these messages explain what the user should do.
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body?.detail === "string") detail = body.detail
    } catch {
      /* non-JSON error body; keep the status line */
    }
    throw new ApiError(detail, response.status)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<Health>("/api/health"),
  summary: () => request<Summary>("/api/summary"),
  orders: (params: { status?: string; q?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams()
    if (params.status && params.status !== "all") qs.set("status", params.status)
    if (params.q) qs.set("q", params.q)
    qs.set("limit", String(params.limit ?? 500))
    return request<{ total: number; returned: number; orders: OrderRow[] }>(
      `/api/orders?${qs}`,
    )
  },
  exceptions: () => request<{ total: number; exceptions: ExceptionRow[] }>("/api/exceptions"),
  findings: () =>
    request<{ total: number; unresolved: number; findings: AgentFinding[] }>("/api/findings"),
  settlements: () =>
    request<{ total: number; settlements: SettlementCheck[] }>("/api/settlements"),
  audit: (limit = 2000) =>
    request<{ total: number; counts: Record<string, number>; events: AuditEvent[] }>(
      `/api/audit?limit=${limit}`,
    ),
  evaluation: () => request<EvalReport>("/api/eval"),
  ask: (question: string, history: { role: string; content: string }[] = []) =>
    request<AskResponse>("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question, history }),
    }),
  investigate: () =>
    request<{
      run_id: string
      agent_ran: boolean
      agent_error: string | null
      cases_investigated: number
      unresolved: number
    }>("/api/investigate", { method: "POST" }),
}
