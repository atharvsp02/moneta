"use client"

import { useMemo, useState } from "react"
import { api } from "@/lib/api"
import { Badge, CategoryChip, Empty, ErrorNote, Mono, Panel, Spinner } from "./primitives"
import { useResource } from "./use-moneta"

const FILTERS = [
  { id: "all", label: "All records" },
  { id: "matched", label: "Matched" },
  { id: "exception", label: "Exceptions" },
  { id: "unsettled", label: "Not yet settled" },
] as const

/** The per-order ledger: what Razorpay settled, what the books say, and the delta.
 *  This is the raw material behind the headline match rate. */
export function Records() {
  const [status, setStatus] = useState<string>("all")
  const [query, setQuery] = useState("")
  const { data, error, loading, reload } = useResource(
    () => api.orders({ status, limit: 1000 }),
    [status],
  )

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return data?.orders ?? []
    return (data?.orders ?? []).filter(
      (r) =>
        r.order_id.toLowerCase().includes(needle) ||
        r.entity_id.toLowerCase().includes(needle) ||
        (r.classification ?? "").toLowerCase().includes(needle),
    )
  }, [data, query])

  return (
    <Panel
      title="Records"
      subtitle="Every settlement row matched against the merchant's books, order by order."
      right={
        <div className="flex shrink-0 items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find an order…"
            className="w-44 rounded-md border border-border bg-foreground/[0.03] px-2.5 py-1.5 text-xs
                       text-foreground placeholder:text-foreground/30 focus:border-primary/40 focus:outline-none"
          />
          <div className="flex rounded-md border border-border p-0.5">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setStatus(f.id)}
                className={`rounded px-2.5 py-1 text-xs transition-colors ${
                  status === f.id
                    ? "bg-foreground/10 text-foreground"
                    : "text-foreground/45 hover:text-foreground/75"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      }
      bodyClassName="max-h-[calc(100vh-19rem)] min-h-[26rem] overflow-auto"
    >
      {loading ? (
        <Spinner />
      ) : error ? (
        <ErrorNote message={error} onRetry={reload} />
      ) : rows.length === 0 ? (
        <Empty>No records match this filter.</Empty>
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="th">Order</th>
              <th className="th">Type</th>
              <th className="th text-right">Razorpay</th>
              <th className="th text-right">Books</th>
              <th className="th text-right">Delta</th>
              <th className="th">Status</th>
              <th className="th">Reason</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.entity_id}-${r.entry_type}`} className="row-hover">
                <td className="td">
                  <Mono className="text-foreground/85">{r.order_id}</Mono>
                </td>
                <td className="td capitalize text-foreground/50">{r.entry_type}</td>
                <td className="td tnum text-right">{r.settlement_amount}</td>
                <td className="td tnum text-right text-foreground/60">{r.books_amount ?? "—"}</td>
                <td
                  className={`td tnum text-right font-medium ${
                    r.delta && r.delta !== "₹0.00" ? "text-[hsl(var(--bad))]" : "text-foreground/30"
                  }`}
                >
                  {r.delta ?? "—"}
                </td>
                <td className="td">
                  {r.matched ? (
                    <Badge tone="ok">Matched</Badge>
                  ) : (
                    <CategoryChip category={r.classification} />
                  )}
                  {!r.settled && (
                    <Badge tone="neutral" className="ml-1.5">
                      unsettled
                    </Badge>
                  )}
                </td>
                <td className="td max-w-[20rem] truncate text-foreground/40">{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
