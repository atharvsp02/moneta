"use client"

import { api } from "@/lib/api"
import { humanize, pct } from "@/lib/format"
import { Badge, CategoryChip, Empty, ErrorNote, Mono, Panel, Spinner, Stat } from "./primitives"
import { useResource } from "./use-moneta"

/**
 * Scores the run against the faults the generator actually injected. This is the
 * section that keeps the headline match rate honest: it shows what Moneta missed and
 * what it claimed that was not there, not only what it got right.
 */
export function Evaluation() {
  const { data, error, loading, reload } = useResource(() => api.evaluation(), [])

  if (loading) return <Spinner label="Scoring against ground truth…" />
  if (error) return <ErrorNote message={error} onRetry={reload} />
  if (!data) return null

  const t = data.totals

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Detection recall"
          value={pct(t.detection_recall)}
          tone={(t.detection_recall ?? 0) >= 0.9 ? "ok" : "warn"}
          sub={
            <>
              {t.detected} of {t.injected_errors} injected faults flagged
            </>
          }
        />
        <Stat
          label="Attribution accuracy"
          value={pct(t.attribution_accuracy)}
          tone={(t.attribution_accuracy ?? 0) >= 0.9 ? "ok" : "warn"}
          sub={<>{t.correctly_attributed} traced to the right root cause</>}
        />
        <Stat
          label="Micro precision"
          value={pct(t.micro_precision)}
          tone={(t.micro_precision ?? 0) >= 0.95 ? "ok" : "warn"}
          sub={<>{t.unclaimed_predictions} claims with no injected fault behind them</>}
          hint="A false positive here means Moneta flagged something that was not actually wrong."
        />
        <Stat
          label="Missed entirely"
          value={String(t.missed)}
          tone={t.missed === 0 ? "ok" : "bad"}
          sub={
            <>
              {t.misclassified} misclassified · {t.honestly_unresolved} honestly unresolved
            </>
          }
        />
      </div>

      {!data.agent_ran && (
        <div className="rounded-lg border border-[hsl(var(--warn))]/25 bg-[hsl(var(--warn))]/[0.06] px-4 py-3">
          <p className="text-[13px] leading-relaxed text-[hsl(var(--warn))]">
            The investigation agent did not run for this scored run
            {data.agent_error ? `: ${data.agent_error}` : "."} Settlement-scope faults are the
            agent&apos;s to attribute, so they are counted as missed here rather than quietly
            excluded. This is the deterministic floor, not the full system score.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="Per-class precision and recall"
          subtitle="Scored against the generator's label file, on data the engine has never been tuned against."
          bodyClassName="overflow-x-auto"
        >
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="th">Classification</th>
                <th className="th text-right">Support</th>
                <th className="th text-right">TP</th>
                <th className="th text-right">FP</th>
                <th className="th text-right">FN</th>
                <th className="th text-right">Precision</th>
                <th className="th text-right">Recall</th>
                <th className="th text-right">F1</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.per_class).map(([cls, row]) => (
                <tr key={cls} className="row-hover">
                  <td className="td">
                    <CategoryChip category={cls} />
                  </td>
                  <td className="td tnum text-right">{row.support}</td>
                  <td className="td tnum text-right text-[hsl(var(--ok))]">{row.true_positives}</td>
                  <td className="td tnum text-right text-[hsl(var(--bad))]">
                    {row.false_positives}
                  </td>
                  <td className="td tnum text-right text-[hsl(var(--warn))]">
                    {row.false_negatives}
                  </td>
                  <td className="td tnum text-right">{pct(row.precision)}</td>
                  <td className="td tnum text-right">{pct(row.recall)}</td>
                  <td className="td tnum text-right font-medium">{pct(row.f1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <div className="space-y-4">
          <Panel title="Which layer resolved it" bodyClassName="divide-y divide-border">
            {Object.entries(data.by_layer).map(([layer, row]) => (
              <div key={layer} className="flex items-center justify-between px-5 py-3">
                <div>
                  <p className="text-[13px] capitalize text-foreground/75">{layer}</p>
                  <p className="text-[11px] text-foreground/40">
                    {row.correct} correct of {row.attributed} attributed
                  </p>
                </div>
                <Badge tone={(row.accuracy ?? 0) >= 0.9 ? "ok" : row.attributed ? "warn" : "neutral"}>
                  {pct(row.accuracy)}
                </Badge>
              </div>
            ))}
          </Panel>

          <Panel
            title="What it missed"
            subtitle="Listed in full. A held-out score with the misses removed is not a score."
            bodyClassName="divide-y divide-border"
          >
            {data.misses.length === 0 && data.misclassifications.length === 0 ? (
              <Empty>Nothing missed on this dataset.</Empty>
            ) : (
              <>
                {data.misses.map((m) => (
                  <div key={`miss-${m.key}`} className="px-5 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <Mono className="truncate text-foreground/70">{m.key}</Mono>
                      <span className="tnum shrink-0 text-[12px]">{m.expected_impact}</span>
                    </div>
                    <p className="mt-1 text-[11px] text-foreground/40">
                      expected {humanize(m.expected)} — not flagged
                    </p>
                  </div>
                ))}
                {data.misclassifications.map((m) => (
                  <div key={`mis-${m.key}`} className="px-5 py-2.5">
                    <Mono className="truncate text-foreground/70">{m.key}</Mono>
                    <p className="mt-1 text-[11px] text-foreground/40">
                      expected {humanize(m.expected)} — called {humanize(m.predicted)}
                    </p>
                  </div>
                ))}
              </>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}
