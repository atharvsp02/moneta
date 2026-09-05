"use client"

import { api, type AgentFinding, type Summary } from "@/lib/api"
import { colorFor, humanize, pct } from "@/lib/format"
import { Bar, Badge, CategoryChip, Empty, ErrorNote, Panel, Spinner, Stat } from "./primitives"
import { useResource } from "./use-moneta"

export function Overview({ onOpenExceptions }: { onOpenExceptions: () => void }) {
  const summary = useResource<Summary>(() => api.summary(), [])
  const findings = useResource(() => api.findings(), [])

  if (summary.loading) return <Spinner label="Reconciling…" />
  if (summary.error) return <ErrorNote message={summary.error} onRetry={summary.reload} />
  if (!summary.data) return null

  const s = summary.data
  const m = s.match_rate
  const unresolvedFindings = (findings.data?.findings ?? []).filter(
    (f) => f.classification === "UNRESOLVED",
  )

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Match rate by value"
          value={pct(m.match_rate_value)}
          tone={m.match_rate_value >= 0.9 ? "ok" : "warn"}
          sub={
            <>
              {m.value_matched} of {m.value_total} reconciled
            </>
          }
          hint="Settled value that ties out between Razorpay and the merchant's books."
        />
        <Stat
          label="Match rate by record"
          value={pct(m.match_rate_records)}
          tone={m.match_rate_records >= 0.9 ? "ok" : "warn"}
          sub={
            <>
              {m.records_matched} of {m.records_total} records matched
            </>
          }
        />
        <Stat
          label="Value unresolved"
          value={m.value_unresolved}
          tone={m.value_total_paise - m.value_matched_paise > 0 ? "bad" : "ok"}
          sub={<>Across {s.exceptions.total} exceptions — every one is listed, none hidden</>}
        />
        <Stat
          label="Deterministic pass"
          value={`${s.throughput.deterministic_runtime_ms.toFixed(2)} ms`}
          sub={
            <>
              {s.throughput.records_per_second?.toLocaleString() ?? "—"} records/sec · no LLM in the
              matching path
            </>
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <ExceptionBreakdown summary={s} onOpenExceptions={onOpenExceptions} />
        <div className="space-y-4 lg:col-span-2">
          <ResolutionSplit summary={s} />
          <TimingPanel summary={s} />
        </div>
      </div>

      <CouldNotResolve
        findings={unresolvedFindings}
        openForAgent={s.exceptions.open_for_agent}
        agentAvailable={s.agent_available}
        loading={findings.loading}
      />
    </div>
  )
}

function ExceptionBreakdown({
  summary,
  onOpenExceptions,
}: {
  summary: Summary
  onOpenExceptions: () => void
}) {
  const rows = summary.exceptions.breakdown
  const max = Math.max(...rows.map((r) => r.value_paise), 1)

  return (
    <Panel
      className="lg:col-span-3"
      title="Exception breakdown"
      subtitle="By ₹ value at risk. Every exception is categorised — nothing falls into a silent bucket."
      right={
        <button
          onClick={onOpenExceptions}
          className="shrink-0 rounded-md border border-border px-2.5 py-1 text-xs text-foreground/70 transition-colors hover:bg-foreground/5"
        >
          Inspect all
        </button>
      }
      bodyClassName="p-5"
    >
      {rows.length === 0 ? (
        <Empty>No exceptions — every record tied out.</Empty>
      ) : (
        <ul className="space-y-3.5">
          {rows.map((row) => (
            <li key={row.category}>
              <div className="mb-1.5 flex items-baseline justify-between gap-4">
                <div className="flex min-w-0 items-center gap-2">
                  <CategoryChip
                    category={row.category === "OPEN_FOR_INVESTIGATION" ? null : row.category}
                  />
                  <span className="shrink-0 text-xs text-foreground/40">
                    {row.count} {row.count === 1 ? "case" : "cases"}
                  </span>
                </div>
                <span className="tnum shrink-0 text-[13px] font-medium">{row.value}</span>
              </div>
              <Bar
                fraction={row.value_paise / max}
                color={colorFor(
                  row.category === "OPEN_FOR_INVESTIGATION" ? "OPEN_FOR_INVESTIGATION" : row.category,
                )}
              />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

function ResolutionSplit({ summary }: { summary: Summary }) {
  const e = summary.exceptions
  const total = Math.max(e.total, 1)
  const segments = [
    { label: "Closed by rules", n: e.closed_by_rules, color: "#78fcd6" },
    { label: "Attributed by agent", n: Math.max(e.investigated - e.unresolved, 0), color: "#38bdf8" },
    { label: "Honestly unresolved", n: e.unresolved, color: "#ef4444" },
    { label: "Awaiting agent", n: e.open_for_agent, color: "#94a3b8" },
  ].filter((seg) => seg.n > 0)

  return (
    <Panel
      title="How each exception was resolved"
      subtitle="Rules decide matches. The agent only investigates what rules could not attribute."
      bodyClassName="p-5"
    >
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-foreground/[0.06]">
        {segments.map((seg) => (
          <div
            key={seg.label}
            style={{ width: `${(seg.n / total) * 100}%`, backgroundColor: seg.color }}
            title={`${seg.label}: ${seg.n}`}
          />
        ))}
      </div>
      <ul className="mt-4 space-y-2">
        {segments.map((seg) => (
          <li key={seg.label} className="flex items-center justify-between text-[13px]">
            <span className="flex items-center gap-2 text-foreground/70">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: seg.color }} />
              {seg.label}
            </span>
            <span className="tnum font-medium">{seg.n}</span>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function TimingPanel({ summary }: { summary: Summary }) {
  const c = summary.clearing_account
  const t = summary.timing
  return (
    <Panel
      title="Clearing account"
      subtitle="What should still be sitting in Razorpay Clearing, and whether it does."
      bodyClassName="divide-y divide-border"
    >
      <Row label="Balance in books" value={c.actual} />
      <Row label="Explained by T+2 timing" value={c.explained_by_timing} />
      <Row
        label="Unexplained"
        value={c.unexplained}
        tone={c.unexplained_paise === 0 ? "ok" : "bad"}
      />
      <div className="px-5 py-3">
        <p className="text-xs leading-relaxed text-foreground/45">
          {t.unsettled_records} payments worth {t.unsettled_value} were captured but not yet settled
          at the cutoff. {t.note}
        </p>
      </div>
    </Panel>
  )
}

function Row({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: "ok" | "bad"
}) {
  const color =
    tone === "ok"
      ? "text-[hsl(var(--ok))]"
      : tone === "bad"
        ? "text-[hsl(var(--bad))]"
        : "text-foreground"
  return (
    <div className="flex items-center justify-between px-5 py-2.5">
      <span className="text-[13px] text-foreground/60">{label}</span>
      <span className={`tnum text-[13px] font-medium ${color}`}>{value}</span>
    </div>
  )
}

/**
 * The section the brief says must never be hidden or minimised. It renders even when
 * empty, and it states plainly why it is empty.
 */
function CouldNotResolve({
  findings,
  openForAgent,
  agentAvailable,
  loading,
}: {
  findings: AgentFinding[]
  openForAgent: number
  agentAvailable: boolean
  loading: boolean
}) {
  return (
    <Panel
      title="Could not resolve"
      subtitle="Cases Moneta investigated and could not attribute with confidence. Shown in full, deliberately."
      right={
        <Badge tone={findings.length > 0 ? "bad" : openForAgent > 0 ? "warn" : "ok"}>
          {findings.length} unresolved
        </Badge>
      }
      bodyClassName="divide-y divide-border"
    >
      {loading ? (
        <Spinner />
      ) : findings.length === 0 ? (
        <div className="px-5 py-6">
          {openForAgent > 0 ? (
            <p className="text-sm text-foreground/60">
              {openForAgent} {openForAgent === 1 ? "case is" : "cases are"} still open for
              investigation.{" "}
              {agentAvailable
                ? "Run the agent to attribute them."
                : "No model API key is configured, so the agent has not run — these are counted as unresolved, not as matched."}
            </p>
          ) : (
            <p className="text-sm text-foreground/60">
              Every exception was attributed to a root cause. Nothing was left unexplained.
            </p>
          )}
        </div>
      ) : (
        findings.map((f) => (
          <div key={f.case_key} className="px-5 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[12px] text-foreground/70">{f.case_key}</span>
              <Badge tone="bad">Unresolved</Badge>
              <span className="tnum ml-auto text-[13px] font-medium">{f.delta}</span>
            </div>
            <p className="mt-2 text-[13px] leading-relaxed text-foreground/70">{f.explanation}</p>
            {f.error && (
              <p className="mt-1.5 font-mono text-[11px] text-[hsl(var(--bad))]/80">{f.error}</p>
            )}
          </div>
        ))
      )}
    </Panel>
  )
}
