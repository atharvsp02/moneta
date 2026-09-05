"use client"

import { Fragment, useMemo, useState } from "react"
import { api, type ExceptionRow } from "@/lib/api"
import { humanize } from "@/lib/format"
import { Badge, CategoryChip, Empty, ErrorNote, Mono, Panel, SeverityDot, Spinner } from "./primitives"
import { useResource } from "./use-moneta"

const STATUS_LABEL: Record<ExceptionRow["status"], { text: string; tone: "ok" | "warn" | "accent" }> =
  {
    closed_by_rules: { text: "Closed by rules", tone: "ok" },
    investigated: { text: "Agent attributed", tone: "accent" },
    open_for_agent: { text: "Open for agent", tone: "warn" },
  }

export function Exceptions() {
  const { data, error, loading, reload } = useResource(() => api.exceptions(), [])
  const [query, setQuery] = useState("")
  const [category, setCategory] = useState("all")
  const [expanded, setExpanded] = useState<string | null>(null)

  const rows = data?.exceptions ?? []
  const categories = useMemo(
    () => Array.from(new Set(rows.map((r) => r.classification ?? "OPEN"))).sort(),
    [rows],
  )
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return rows.filter((r) => {
      if (category !== "all" && (r.classification ?? "OPEN") !== category) return false
      if (!needle) return true
      return (
        r.key.toLowerCase().includes(needle) ||
        r.exception_id.toLowerCase().includes(needle) ||
        r.rule.toLowerCase().includes(needle)
      )
    })
  }, [rows, query, category])

  if (loading) return <Spinner />
  if (error) return <ErrorNote message={error} onRetry={reload} />

  return (
    <Panel
      title="Exceptions"
      subtitle="Every discrepancy the engine found, largest ₹ delta first. Click a row for its evidence."
      right={
        <div className="flex shrink-0 items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by id or rule…"
            className="w-48 rounded-md border border-border bg-foreground/[0.03] px-2.5 py-1.5 text-xs
                       text-foreground placeholder:text-foreground/30 focus:border-primary/40 focus:outline-none"
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="rounded-md border border-border bg-foreground/[0.03] px-2.5 py-1.5 text-xs
                       text-foreground focus:border-primary/40 focus:outline-none"
          >
            <option value="all">All categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {humanize(c === "OPEN" ? null : c)}
              </option>
            ))}
          </select>
        </div>
      }
      bodyClassName="overflow-x-auto"
    >
      {filtered.length === 0 ? (
        <Empty>No exceptions match this filter.</Empty>
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="th">Key</th>
              <th className="th">Category</th>
              <th className="th">Scope</th>
              <th className="th text-right">Delta</th>
              <th className="th">Resolution</th>
              <th className="th">Rule</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => {
              const open = expanded === row.exception_id
              const status = STATUS_LABEL[row.status]
              return (
                <Fragment key={row.exception_id}>
                  <tr

                    onClick={() => setExpanded(open ? null : row.exception_id)}
                    className="row-hover cursor-pointer"
                  >
                    <td className="td">
                      <span className="flex items-center gap-2">
                        <SeverityDot classification={row.classification} />
                        <Mono className="text-foreground/85">{row.key}</Mono>
                      </span>
                    </td>
                    <td className="td">
                      <CategoryChip category={row.classification} />
                    </td>
                    <td className="td text-foreground/50">{row.scope}</td>
                    <td className="td tnum text-right font-medium">{row.delta}</td>
                    <td className="td">
                      <Badge tone={status.tone}>{status.text}</Badge>
                    </td>
                    <td className="td max-w-[22rem] truncate font-mono text-[11px] text-foreground/40">
                      {row.rule}
                    </td>
                  </tr>
                  {open && (
                    <tr key={`${row.exception_id}-detail`} className="border-t border-border/60">
                      <td colSpan={6} className="bg-foreground/[0.02] px-4 py-4">
                        <ExceptionDetail row={row} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}
    </Panel>
  )
}

function ExceptionDetail({ row }: { row: ExceptionRow }) {
  const finding = row.agent_finding
  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-foreground/40">
          Evidence the rules engine recorded
        </p>
        <ul className="space-y-2">
          {row.evidence.map((ev, i) => (
            <li key={i} className="rounded-lg border border-border bg-background/40 px-3 py-2.5">
              <div className="flex items-center gap-2">
                <Badge tone={ev.source === "settlement" ? "accent" : "neutral"}>{ev.source}</Badge>
                <span className="text-[13px] text-foreground/75">{ev.detail}</span>
              </div>
              <pre className="mt-1.5 overflow-x-auto font-mono text-[11px] leading-relaxed text-foreground/45">
                {JSON.stringify(ev.data, null, 2)}
              </pre>
            </li>
          ))}
        </ul>
      </div>

      {finding && (
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-foreground/40">
            Agent investigation
          </p>
          <div className="rounded-lg border border-primary/20 bg-primary/[0.04] px-3 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <CategoryChip category={finding.classification} />
              <Badge tone={finding.confidence === "high" ? "ok" : "warn"}>
                {finding.confidence} confidence
              </Badge>
              <span className="ml-auto text-[11px] text-foreground/40">
                {finding.turns} turns · {finding.tool_calls.length} tool calls ·{" "}
                {(finding.duration_ms / 1000).toFixed(1)}s
              </span>
            </div>
            <p className="mt-2.5 text-[13px] leading-relaxed text-foreground/80">
              {finding.explanation}
            </p>
            {finding.evidence.length > 0 && (
              <ul className="mt-2.5 space-y-1">
                {finding.evidence.map((e, i) => (
                  <li key={i} className="flex gap-2 text-[12px] leading-relaxed text-foreground/55">
                    <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-primary/60" />
                    {e}
                  </li>
                ))}
              </ul>
            )}
            {finding.recommended_action && (
              <div className="mt-3 rounded-md border border-border bg-background/50 px-3 py-2">
                <p className="text-[11px] font-medium uppercase tracking-wider text-foreground/40">
                  Recommended action — for a human to take
                </p>
                <p className="mt-1 text-[13px] leading-relaxed text-foreground/75">
                  {finding.recommended_action}
                </p>
              </div>
            )}
            {finding.tool_calls.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {finding.tool_calls.map((tc, i) => (
                  <span
                    key={i}
                    className="rounded border border-border bg-background/60 px-1.5 py-0.5 font-mono text-[10px] text-foreground/45"
                  >
                    {tc.tool}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {row.status === "open_for_agent" && !finding && (
        <p className="text-[13px] text-foreground/50">
          The rules engine quantified this delta but could not attribute a cause. It is queued for
          the investigation agent and is counted as unresolved until the agent explains it.
        </p>
      )}
    </div>
  )
}
