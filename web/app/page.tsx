"use client"

import { useState } from "react"
import { api } from "@/lib/api"
import { Ask } from "@/components/dashboard/ask"
import { Audit } from "@/components/dashboard/audit"
import { Evaluation } from "@/components/dashboard/evaluation"
import { Exceptions } from "@/components/dashboard/exceptions"
import { Overview } from "@/components/dashboard/overview"
import { Records } from "@/components/dashboard/records"
import { Badge, ErrorNote, Spinner } from "@/components/dashboard/primitives"
import { useResource } from "@/components/dashboard/use-moneta"

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "exceptions", label: "Exceptions" },
  { id: "records", label: "Records" },
  { id: "ask", label: "Ask Moneta" },
  { id: "eval", label: "Evaluation" },
  { id: "audit", label: "Audit trail" },
] as const

type TabId = (typeof TABS)[number]["id"]

export default function Dashboard() {
  const [tab, setTab] = useState<TabId>("overview")
  const health = useResource(() => api.health(), [])

  return (
    <div className="min-h-screen bg-background">
      <Header
        dataset={health.data?.dataset}
        model={health.data?.model}
        agentAvailable={health.data?.agent_available ?? false}
        rows={health.data?.settlement_rows}
        cycles={health.data?.settlement_cycles}
      />

      <nav className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] gap-1 px-6">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`relative px-3 py-3 text-[13px] transition-colors ${
                tab === t.id
                  ? "text-foreground"
                  : "text-foreground/45 hover:text-foreground/75"
              }`}
            >
              {t.label}
              {tab === t.id && (
                <span className="absolute inset-x-2 -bottom-px h-px bg-primary" />
              )}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-[1400px] px-6 py-6">
        {health.loading ? (
          <Spinner label="Connecting to the Moneta API…" />
        ) : health.error ? (
          <div className="panel">
            <ErrorNote message={health.error} onRetry={health.reload} />
            <div className="px-5 pb-5">
              <p className="text-[13px] leading-relaxed text-foreground/55">
                Start the backend from the project root:
              </p>
              <pre className="mt-2 rounded-lg border border-border bg-foreground/[0.03] px-3.5 py-2.5 font-mono text-[12px] text-primary/90">
                moneta serve --name dev
              </pre>
            </div>
          </div>
        ) : (
          <>
            {tab === "overview" && <Overview onOpenExceptions={() => setTab("exceptions")} />}
            {tab === "exceptions" && <Exceptions />}
            {tab === "records" && <Records />}
            {tab === "ask" && <Ask agentAvailable={health.data?.agent_available ?? false} />}
            {tab === "eval" && <Evaluation />}
            {tab === "audit" && <Audit />}
          </>
        )}
      </main>

      <footer className="mx-auto max-w-[1400px] px-6 pb-8">
        <p className="border-t border-border pt-4 text-[11px] leading-relaxed text-foreground/35">
          Moneta detects, explains and flags. It never writes to the merchant&apos;s ledger, never
          posts a journal entry and never moves money — every tool in the agent&apos;s hands is
          read-only, by design. Recommended actions are for a human to carry out.
        </p>
      </footer>
    </div>
  )
}

function Header({
  dataset,
  model,
  agentAvailable,
  rows,
  cycles,
}: {
  dataset?: string
  model?: string
  agentAvailable: boolean
  rows?: number
  cycles?: number
}) {
  return (
    <header className="border-b border-border">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-2 px-6 py-5">
        <div className="flex items-baseline gap-2.5">
          <h1 className="text-[17px] font-semibold tracking-tight">Moneta</h1>
          <span className="text-[13px] text-foreground/45">Settlement intelligence</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {dataset && (
            <Badge tone="neutral">
              dataset <span className="font-mono text-foreground/80">{dataset}</span>
            </Badge>
          )}
          {rows !== undefined && (
            <Badge tone="neutral">
              {rows} rows · {cycles} cycles
            </Badge>
          )}
          <Badge tone={agentAvailable ? "accent" : "warn"}>
            {agentAvailable ? model ?? "agent online" : "agent offline"}
          </Badge>
          <Badge tone="ok">read-only</Badge>
        </div>
      </div>
      <div className="mx-auto max-w-[1400px] px-6 pb-5">
        <p className="max-w-3xl text-[13px] leading-relaxed text-foreground/50">
          Razorpay settles hundreds of orders as one net bank credit, minus MDR, minus GST on that
          MDR, minus refunds. Moneta unpacks that lump sum back into its parts, ties it against the
          merchant&apos;s own books, and explains every rupee that doesn&apos;t line up.
        </p>
      </div>
    </header>
  )
}
