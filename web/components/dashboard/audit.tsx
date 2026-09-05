"use client"

import { useMemo, useState } from "react"
import { api, type AuditEvent } from "@/lib/api"
import { clockTime, humanize } from "@/lib/format"
import { Badge, Empty, ErrorNote, Mono, Panel, Spinner } from "./primitives"
import { useResource } from "./use-moneta"

/** Events that represent a decision about money, highlighted against routine bookkeeping. */
const EVENT_TONE: Record<string, "ok" | "warn" | "bad" | "accent" | "neutral"> = {
  run_started: "neutral",
  dataset_loaded: "neutral",
  match_decision: "neutral",
  exception_detected: "warn",
  deterministic_pass_complete: "ok",
  investigation_queue_built: "neutral",
  investigation_started: "accent",
  tool_call: "accent",
  finding_recorded: "ok",
  agent_unavailable: "bad",
  run_completed: "ok",
}

export function Audit() {
  const { data, error, loading, reload } = useResource(() => api.audit(), [])
  const [filter, setFilter] = useState("all")
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<number | null>(null)

  const events = data?.events ?? []
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return events.filter((e) => {
      if (filter !== "all" && e.event !== filter) return false
      if (!needle) return true
      return JSON.stringify(e).toLowerCase().includes(needle)
    })
  }, [events, filter, query])

  if (loading) return <Spinner />
  if (error) return <ErrorNote message={error} onRetry={reload} />

  return (
    <Panel
      title="Audit trail"
      subtitle="Every match decision, exception, tool call and finding, in the order it happened. Written to out/*.audit.jsonl."
      right={
        <div className="flex shrink-0 items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search events…"
            className="w-44 rounded-md border border-border bg-foreground/[0.03] px-2.5 py-1.5 text-xs
                       text-foreground placeholder:text-foreground/30 focus:border-primary/40 focus:outline-none"
          />
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="rounded-md border border-border bg-foreground/[0.03] px-2.5 py-1.5 text-xs
                       text-foreground focus:border-primary/40 focus:outline-none"
          >
            <option value="all">All events ({events.length})</option>
            {Object.entries(data?.counts ?? {}).map(([name, n]) => (
              <option key={name} value={name}>
                {humanize(name)} ({n})
              </option>
            ))}
          </select>
        </div>
      }
      bodyClassName="max-h-[calc(100vh-19rem)] min-h-[26rem] overflow-y-auto"
    >
      {filtered.length === 0 ? (
        <Empty>
          {events.length === 0
            ? "No audit trail on disk yet. Run `moneta reconcile` to produce one."
            : "No events match this filter."}
        </Empty>
      ) : (
        <ul className="divide-y divide-border/60">
          {filtered.map((e, i) => (
            <AuditRow
              key={i}
              event={e}
              open={expanded === i}
              onToggle={() => setExpanded(expanded === i ? null : i)}
            />
          ))}
        </ul>
      )}
    </Panel>
  )
}

function AuditRow({
  event,
  open,
  onToggle,
}: {
  event: AuditEvent
  open: boolean
  onToggle: () => void
}) {
  const { ts, run_id, event: name, ...payload } = event
  const tone = EVENT_TONE[name] ?? "neutral"
  const summary = summarize(name, payload)

  return (
    <li>
      <button
        onClick={onToggle}
        className="flex w-full items-baseline gap-3 px-5 py-2 text-left transition-colors hover:bg-foreground/[0.03]"
      >
        <Mono className="shrink-0 text-foreground/30">{clockTime(ts)}</Mono>
        <Badge tone={tone} className="shrink-0">
          {name}
        </Badge>
        <span className="truncate text-[12px] text-foreground/55">{summary}</span>
      </button>
      {open && (
        <pre className="overflow-x-auto bg-foreground/[0.02] px-5 py-3 font-mono text-[11px] leading-relaxed text-foreground/50">
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </li>
  )
}

/** A one-line gist per event type, so the trail is scannable without expanding rows. */
function summarize(name: string, payload: Record<string, unknown>): string {
  const s = (k: string) => (payload[k] === undefined ? "" : String(payload[k]))
  switch (name) {
    case "match_decision":
      return `${s("order_id")} — ${payload.matched ? "matched" : "EXCEPTION"} · ${s("reason")}`
    case "exception_detected":
      return `${s("key")} — ${s("classification") || "open for agent"} · ${s("delta")}`
    case "tool_call":
      return `${s("tool")}(${JSON.stringify(payload.arguments ?? {})})`
    case "finding_recorded":
      return `${s("case_key")} — ${s("classification")} (${s("confidence")})`
    case "investigation_started":
      return `${s("case_key")} — ${s("family")}`
    case "deterministic_pass_complete":
      return `${s("records_matched")}/${s("records_total")} matched in ${s("runtime_ms")}ms`
    case "run_completed":
      return `${s("value_matched")} of ${s("value_total")} reconciled`
    case "agent_unavailable":
      return s("reason")
    default:
      return Object.entries(payload)
        .slice(0, 3)
        .map(([k, v]) => `${k}=${typeof v === "object" ? "…" : v}`)
        .join(" · ")
  }
}
