"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { pct } from "@/lib/format"

/**
 * Verified figures from the committed run artefacts, used when the API is not running —
 * a judge opening this page from a clone should still see real numbers rather than
 * dashes. Sources: out/dev.report.json and out/holdout.eval.json.
 *
 * When the API *is* up, the live values replace these. They should agree; if they ever
 * diverge, the live number is the true one and these are stale.
 */
const VERIFIED = {
  matchRateValue: 0.9362,
  matchRateRecords: 0.9274,
  valueMatched: "₹428,539.23",
  valueTotal: "₹457,722.91",
  valueUnresolved: "₹29,183.68",
  runtimeMs: 0.74,
  records: 124,
  cycles: 19,
  microPrecision: 1.0,
}

export function Nav() {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/80 backdrop-blur">
      <div className="mx-auto flex max-w-[1200px] items-center gap-6 px-6 py-3.5">
        <span className="text-[15px] font-semibold tracking-tight">Moneta</span>
        <nav className="hidden gap-5 md:flex">
          {[
            ["The problem", "#problem"],
            ["How it works", "#how"],
            ["Results", "#results"],
            ["FAQ", "#faq"],
          ].map(([label, href]) => (
            <a
              key={href}
              href={href}
              className="text-[13px] text-foreground/50 transition-colors hover:text-foreground"
            >
              {label}
            </a>
          ))}
        </nav>
        <Link
          href="/dashboard"
          className="ml-auto rounded-lg bg-primary px-3.5 py-1.5 text-[13px] font-medium
                     text-primary-foreground transition-opacity hover:opacity-90"
        >
          Open dashboard
        </Link>
      </div>
    </header>
  )
}

export function Hero() {
  return (
    <section className="relative overflow-hidden px-6 pb-16 pt-20 md:pt-28">
      {/* Soft mint bloom behind the headline, matching the template's accent. */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-0 h-[420px] w-[820px] -translate-x-1/2
                   rounded-full opacity-[0.13] blur-[120px]"
        style={{ background: "radial-gradient(circle, #78fcd6 0%, transparent 70%)" }}
      />
      <div className="relative mx-auto max-w-[860px] text-center">
        <span className="inline-flex items-center gap-2 rounded-full border border-border bg-foreground/[0.03] px-3 py-1 text-[12px] text-foreground/55">
          Razorpay AI Buildathon 2026 · Track 04 — AI Finance Controller
        </span>

        <h1 className="mt-6 text-[38px] font-semibold leading-[1.1] tracking-tight md:text-[56px]">
          Razorpay settles 150 orders
          <br />
          as <span className="text-primary">one lump sum</span>.
        </h1>
        <p className="mx-auto mt-5 max-w-[620px] text-[15px] leading-relaxed text-foreground/60 md:text-[17px]">
          Moneta unpacks it back into the orders, fees and tax that make it up, ties that
          against the merchant&apos;s own books, and — unlike a rules engine — explains itself
          when you ask why something doesn&apos;t match.
        </p>

        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/dashboard"
            className="rounded-lg bg-primary px-5 py-2.5 text-[14px] font-medium text-primary-foreground
                       transition-opacity hover:opacity-90"
          >
            Open the dashboard
          </Link>
          <a
            href="#problem"
            className="rounded-lg border border-border px-5 py-2.5 text-[14px] text-foreground/70
                       transition-colors hover:bg-foreground/5 hover:text-foreground"
          >
            See the problem
          </a>
        </div>
      </div>
    </section>
  )
}

/** Headline metrics. Live when the API is up, verified-static when it is not. */
export function LiveStats() {
  const [live, setLive] = useState<typeof VERIFIED | null>(null)
  const [isLive, setIsLive] = useState(false)

  useEffect(() => {
    api
      .summary()
      .then((s) => {
        setLive({
          ...VERIFIED,
          matchRateValue: s.match_rate.match_rate_value,
          matchRateRecords: s.match_rate.match_rate_records,
          valueMatched: s.match_rate.value_matched,
          valueTotal: s.match_rate.value_total,
          valueUnresolved: s.match_rate.value_unresolved,
          runtimeMs: s.throughput.deterministic_runtime_ms,
          records: s.match_rate.records_total,
        })
        setIsLive(true)
      })
      .catch(() => setIsLive(false))
  }, [])

  const v = live ?? VERIFIED

  const stats = [
    {
      value: pct(v.matchRateValue),
      label: "of value reconciled",
      note: `${v.valueMatched} of ${v.valueTotal}`,
      tone: "text-primary",
    },
    {
      value: v.valueUnresolved,
      label: "unresolved — and shown",
      note: "Never rounded away or hidden",
      tone: "text-[hsl(var(--warn))]",
    },
    {
      value: `${v.runtimeMs.toFixed(2)} ms`,
      label: "deterministic pass",
      note: `${v.records} records · no LLM in the matching path`,
      tone: "text-foreground",
    },
    {
      value: pct(v.microPrecision, 0),
      label: "precision on held-out set",
      note: "Zero false alarms against ground truth",
      tone: "text-primary",
    },
  ]

  return (
    <section className="px-6 pb-4">
      <div className="mx-auto max-w-[1100px]">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {stats.map((s) => (
            <div key={s.label} className="panel px-5 py-5">
              <p className={`tnum text-[26px] font-semibold leading-none ${s.tone}`}>{s.value}</p>
              <p className="mt-2 text-[13px] text-foreground/70">{s.label}</p>
              <p className="mt-1 text-[11px] leading-relaxed text-foreground/40">{s.note}</p>
            </div>
          ))}
        </div>
        <p className="mt-3 text-center text-[11px] text-foreground/30">
          {isLive
            ? "Live from the running reconciliation API."
            : "From the committed run artefacts. Start the API to see these live."}
        </p>
      </div>
    </section>
  )
}

/** The settlement anatomy — the thing that makes the problem legible in one glance. */
export function Problem() {
  const rows = [
    { label: "Gross payments", detail: "113 orders across 19 cycles", sign: "+", tone: "text-foreground" },
    { label: "MDR fee", detail: "per method — UPI 0%, cards 2%", sign: "−", tone: "text-[hsl(var(--warn))]" },
    { label: "GST on that fee", detail: "18%, rounded per transaction", sign: "−", tone: "text-[hsl(var(--warn))]" },
    { label: "Refunds netted out", detail: "possibly issued in an earlier cycle", sign: "−", tone: "text-[hsl(var(--warn))]" },
  ]

  return (
    <section id="problem" className="scroll-mt-16 px-6 py-20">
      <div className="mx-auto max-w-[1100px]">
        <div className="grid gap-10 lg:grid-cols-2 lg:items-center">
          <div>
            <p className="text-[12px] font-medium uppercase tracking-wider text-primary/80">
              The problem
            </p>
            <h2 className="mt-3 text-[28px] font-semibold leading-tight tracking-tight md:text-[34px]">
              The money doesn&apos;t arrive order by order.
            </h2>
            <div className="mt-5 space-y-4 text-[14px] leading-relaxed text-foreground/60">
              <p>
                Every settlement cycle, a merchant gets <strong className="font-medium text-foreground/85">one net bank credit</strong>{" "}
                covering hundreds of orders. Someone in finance has to work backwards from that
                single number and prove it matches the ledger.
              </p>
              <p>
                It&apos;s slow, it&apos;s error-prone, and it&apos;s where money quietly goes
                missing — a fee booked at the wrong rate, a refund recorded in the wrong period, an
                order entered twice.
              </p>
              <p className="border-l-2 border-primary/40 pl-4 text-foreground/70">
                Razorpay&apos;s <strong className="font-medium">Optimizer</strong> already
                reconciles settlements against the <strong className="font-medium">bank</strong>.
                Moneta is the layer above: settlements against the merchant&apos;s{" "}
                <strong className="font-medium">own books</strong>.
              </p>
            </div>
          </div>

          <div className="panel p-6">
            <p className="text-[11px] font-medium uppercase tracking-wider text-foreground/40">
              One settlement, decomposed
            </p>
            <ul className="mt-4 space-y-3">
              {rows.map((r) => (
                <li key={r.label} className="flex items-baseline gap-3">
                  <span className={`tnum w-4 shrink-0 text-[15px] ${r.tone}`}>{r.sign}</span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] text-foreground/85">{r.label}</p>
                    <p className="text-[11px] text-foreground/40">{r.detail}</p>
                  </div>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex items-baseline gap-3 border-t border-border pt-4">
              <span className="tnum w-4 shrink-0 text-[15px] text-primary">=</span>
              <div>
                <p className="text-[13px] font-medium text-primary">One net bank credit</p>
                <p className="text-[11px] text-foreground/40">
                  What actually lands in the account, ~T+2
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

const PILLARS = [
  {
    title: "Rules decide. The model explains.",
    body: "Matching is exact integer comparison in paise, in Python. The model is never asked whether two amounts are equal — it only investigates what the engine quantified but could not attribute. A rules engine can't hallucinate ₹4,000 into existence, and a model that never touches the arithmetic can't hallucinate it away.",
    accent: true,
  },
  {
    title: "Ask it why.",
    body: "\"Why doesn't order 4021 match?\" It pulls the payment, the vouchers, the fee breakdown and adjacent settlement cycles, then answers with the evidence it found. The dashboard shows every tool call it made, so a grounded answer is visibly different from a plausible one.",
  },
  {
    title: "It says when it doesn't know.",
    body: "A plausible-sounding wrong attribution is worse than an honest non-answer, because someone acts on it. Cases it can't attribute are reported UNRESOLVED with what it checked and what was missing — on the front page, not in a log file.",
  },
  {
    title: "Eleven tools. All eleven are reads.",
    body: "There's no write path to disable. Moneta cannot post a journal entry, adjust a voucher or move money, because no such capability was built. Every recommended action is for a human to carry out.",
  },
  {
    title: "Scored against ground truth.",
    body: "The data generator records every fault it injects. The eval harness replays a run against that file and reports detection recall separately from attribution accuracy — plus every miss, by name and rupee impact.",
  },
  {
    title: "Every decision on the record.",
    body: "Append-only JSONL: each match with its reason, each exception with its evidence, each tool call with its arguments and duration, each finding with its confidence.",
  },
]

export function HowItWorks() {
  return (
    <section id="how" className="scroll-mt-16 border-y border-border bg-foreground/[0.015] px-6 py-20">
      <div className="mx-auto max-w-[1100px]">
        <p className="text-[12px] font-medium uppercase tracking-wider text-primary/80">
          How it works
        </p>
        <h2 className="mt-3 max-w-[640px] text-[28px] font-semibold leading-tight tracking-tight md:text-[34px]">
          Two layers, and the boundary between them is enforced.
        </h2>

        <div className="mt-10 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {PILLARS.map((p) => (
            <div
              key={p.title}
              className={`panel p-5 ${p.accent ? "border-primary/25 bg-primary/[0.04] md:col-span-2" : ""}`}
            >
              <h3 className="text-[15px] font-medium text-foreground">{p.title}</h3>
              <p className="mt-2 text-[13px] leading-relaxed text-foreground/55">{p.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

/** The live case from the demo: two deltas that only make sense together. */
export function HardCase() {
  return (
    <section className="px-6 py-20">
      <div className="mx-auto max-w-[900px]">
        <p className="text-[12px] font-medium uppercase tracking-wider text-primary/80">
          The case a rules engine can&apos;t close
        </p>
        <h2 className="mt-3 text-[28px] font-semibold leading-tight tracking-tight md:text-[34px]">
          Two settlements. Same number. Opposite signs.
        </h2>
        <p className="mt-4 max-w-[640px] text-[14px] leading-relaxed text-foreground/60">
          The deterministic engine knows the amount is wrong on both. It has no way to know
          they&apos;re one event.
        </p>

        <div className="panel mt-7 overflow-hidden">
          <div className="divide-y divide-border">
            {[
              { utr: "079593718587", delta: "−₹513.17", tone: "text-[hsl(var(--bad))]" },
              { utr: "594996572458", delta: "+₹513.17", tone: "text-[hsl(var(--ok))]" },
            ].map((r) => (
              <div key={r.utr} className="flex items-center gap-4 px-5 py-3.5">
                <span className="font-mono text-[13px] text-foreground/75">{r.utr}</span>
                <span className={`tnum ml-auto text-[14px] font-medium ${r.tone}`}>{r.delta}</span>
                <span className="hidden font-mono text-[11px] text-foreground/30 sm:inline">
                  booked_bank_credit_not_equal_to_reconstructed_net
                </span>
              </div>
            ))}
          </div>
          <div className="border-t border-primary/20 bg-primary/[0.04] px-5 py-4">
            <p className="text-[11px] font-medium uppercase tracking-wider text-primary/70">
              Moneta, after four tool calls
            </p>
            <p className="mt-2 text-[13px] leading-relaxed text-foreground/80">
              The books recorded the credit note on the day the refund was{" "}
              <strong className="font-medium">issued</strong>. Razorpay netted it out of the{" "}
              <strong className="font-medium">following</strong> settlement cycle. One event booked
              in two periods — which is exactly why it appears as two equal and opposite deltas.
            </p>
            <p className="mt-2.5 text-[12px] text-foreground/45">
              It had to go <em>look</em> for where the money went. That&apos;s the part a rules
              engine structurally cannot do.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}

export function Results() {
  const rows = [
    ["AMOUNT_MISMATCH", "3", "100%", "100%"],
    ["DUPLICATE_BOOKING", "2", "100%", "100%"],
    ["MISSING_IN_BOOKS", "2", "100%", "100%"],
    ["MISSING_REFUND_IN_BOOKS", "2", "100%", "100%"],
  ]

  return (
    <section id="results" className="scroll-mt-16 border-y border-border bg-foreground/[0.015] px-6 py-20">
      <div className="mx-auto max-w-[1100px]">
        <p className="text-[12px] font-medium uppercase tracking-wider text-primary/80">Results</p>
        <h2 className="mt-3 text-[28px] font-semibold leading-tight tracking-tight md:text-[34px]">
          Measured on a held-out set, misses included.
        </h2>
        <p className="mt-4 max-w-[660px] text-[14px] leading-relaxed text-foreground/60">
          Different seed, data the engine was never tuned against. The generator knows exactly which
          faults it injected, so every number here is scored against ground truth — not counted by
          hand.
        </p>

        <div className="mt-9 grid gap-4 lg:grid-cols-5">
          <div className="panel overflow-x-auto lg:col-span-3">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className="th">Classification</th>
                  <th className="th text-right">n</th>
                  <th className="th text-right">Precision</th>
                  <th className="th text-right">Recall</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([cls, n, p, r]) => (
                  <tr key={cls} className="row-hover">
                    <td className="td font-mono text-[12px]">{cls}</td>
                    <td className="td tnum text-right">{n}</td>
                    <td className="td tnum text-right text-[hsl(var(--ok))]">{p}</td>
                    <td className="td tnum text-right text-[hsl(var(--ok))]">{r}</td>
                  </tr>
                ))}
                <tr className="row-hover">
                  <td className="td font-mono text-[12px] text-foreground/45">
                    AGGREGATE_FEE_MISMATCH
                  </td>
                  <td className="td tnum text-right text-foreground/45">2</td>
                  <td className="td tnum text-right text-foreground/30">—</td>
                  <td className="td tnum text-right text-[hsl(var(--warn))]">0%</td>
                </tr>
                <tr className="row-hover">
                  <td className="td font-mono text-[12px] text-foreground/45">CROSS_CYCLE_REFUND</td>
                  <td className="td tnum text-right text-foreground/45">1</td>
                  <td className="td tnum text-right text-foreground/30">—</td>
                  <td className="td tnum text-right text-[hsl(var(--warn))]">0%</td>
                </tr>
              </tbody>
            </table>
            <p className="border-t border-border px-4 py-3 text-[11px] leading-relaxed text-foreground/45">
              The bottom two rows are the settlement-scope faults the deterministic layer
              deliberately does <em>not</em> attribute — it quantifies the delta and hands the case
              to the investigation agent. Scored with the agent disabled, they count as misses. This
              is the honest deterministic floor, shown rather than trimmed.
            </p>
          </div>

          <div className="space-y-3 lg:col-span-2">
            <div className="panel px-5 py-5">
              <p className="tnum text-[30px] font-semibold leading-none text-primary">100%</p>
              <p className="mt-2 text-[13px] text-foreground/70">micro precision</p>
              <p className="mt-1.5 text-[12px] leading-relaxed text-foreground/45">
                Zero unclaimed predictions — it never flagged something that wasn&apos;t actually
                wrong. For a tool a controller acts on, a false alarm costs more than a known gap.
              </p>
            </div>
            <div className="panel px-5 py-5">
              <p className="tnum text-[30px] font-semibold leading-none">9 / 13</p>
              <p className="mt-2 text-[13px] text-foreground/70">injected faults detected</p>
              <p className="mt-1.5 text-[12px] leading-relaxed text-foreground/45">
                Every one of the four misses is listed by name and rupee impact in the dashboard. A
                held-out score with the failures removed isn&apos;t a score.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

const FAQS = [
  {
    q: "Isn't this just Optimizer?",
    a: "No. Optimizer matches settlements to the bank statement, across gateways — that part is solved and we didn't rebuild it. Moneta matches settlements to the sales ledger, with the MDR and GST breakdown unpacked. The RazorpayX–Tally integration covers the accounts-payable side. This is the gap between them.",
  },
  {
    q: "How do you know the LLM isn't making up the numbers?",
    a: "It structurally cannot affect them. Every match and every delta is computed in Python in integer paise before the model is invoked. The model receives cases that are already quantified; its output is a classification and an explanation. It cannot change a match, a delta or a total. A dedicated tool computes fee and GST breakdowns so it never needs arithmetic to reach a conclusion.",
  },
  {
    q: "What happens when it's wrong?",
    a: "It should say UNRESOLVED, and it's instructed that a plausible wrong answer is worse than an honest non-answer. The eval measures exactly this — unclaimed predictions are counted as false positives, and that number is currently zero.",
  },
  {
    q: "Would you let it fix the ledger?",
    a: "Not as built, and that's deliberate. Confidence isn't high enough for autonomous writes on financial records, and the audit story is far stronger with a human as the actor. The right next step is a proposed journal entry a controller approves — still a human in the loop.",
  },
  {
    q: "What are the limitations?",
    a: "The data is synthetic — it mirrors Razorpay's real settlement schema and a realistic Tally-style ledger, but real books are messier. The rate card is fixed. Matching assumes the gateway order id is carried into the ledger, which real merchants often don't do. The agent is scored on seven injected fault classes; novel causes outside those would land as UNRESOLVED. All of this is written up in the README.",
  },
]

export function FAQ() {
  const [open, setOpen] = useState<number | null>(0)
  return (
    <section id="faq" className="scroll-mt-16 px-6 py-20">
      <div className="mx-auto max-w-[760px]">
        <h2 className="text-[28px] font-semibold leading-tight tracking-tight md:text-[34px]">
          Questions worth asking
        </h2>
        <div className="mt-8 divide-y divide-border border-y border-border">
          {FAQS.map((f, i) => (
            <div key={f.q}>
              <button
                onClick={() => setOpen(open === i ? null : i)}
                className="flex w-full items-center gap-4 py-4 text-left"
              >
                <span className="flex-1 text-[15px] font-medium text-foreground/90">{f.q}</span>
                <span
                  className={`shrink-0 text-foreground/35 transition-transform ${open === i ? "rotate-45" : ""}`}
                >
                  +
                </span>
              </button>
              {open === i && (
                <p className="pb-5 pr-8 text-[13px] leading-relaxed text-foreground/55">{f.a}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export function CTA() {
  return (
    <section className="px-6 pb-20">
      <div className="panel mx-auto max-w-[900px] px-8 py-12 text-center">
        <h2 className="text-[26px] font-semibold tracking-tight md:text-[32px]">
          Hours of manual work per cycle, in under a second.
        </h2>
        <p className="mx-auto mt-4 max-w-[560px] text-[14px] leading-relaxed text-foreground/55">
          With a full audit trail, and complete honesty about what it couldn&apos;t resolve.
        </p>
        <Link
          href="/dashboard"
          className="mt-7 inline-block rounded-lg bg-primary px-6 py-2.5 text-[14px] font-medium
                     text-primary-foreground transition-opacity hover:opacity-90"
        >
          Open the dashboard
        </Link>
      </div>
    </section>
  )
}

export function Footer() {
  return (
    <footer className="border-t border-border px-6 py-8">
      <div className="mx-auto max-w-[1100px]">
        <p className="text-[12px] leading-relaxed text-foreground/40">
          <strong className="font-medium text-foreground/60">Moneta detects, explains and flags.</strong>{" "}
          It never writes to the merchant&apos;s ledger, never posts a journal entry and never moves
          money — every tool in the agent&apos;s hands is read-only, by construction. Recommended
          actions are for a human to carry out.
        </p>
        <p className="mt-4 text-[11px] text-foreground/25">
          Named for Juno Moneta, the Roman goddess whose temple minted Rome&apos;s currency and gave
          us both &ldquo;money&rdquo; and &ldquo;mint&rdquo;. · Razorpay AI Buildathon 2026 · Data is
          synthetic.
        </p>
      </div>
    </footer>
  )
}
