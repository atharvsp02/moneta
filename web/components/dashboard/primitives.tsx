"use client"

import { cn } from "@/lib/utils"
import { colorFor, humanize, severityOf } from "@/lib/format"

export function Panel({
  title,
  subtitle,
  right,
  className,
  bodyClassName,
  children,
}: {
  title?: string
  subtitle?: string
  right?: React.ReactNode
  className?: string
  bodyClassName?: string
  children: React.ReactNode
}) {
  return (
    <section className={cn("panel", className)}>
      {(title || right) && (
        <header className="panel-header">
          <div>
            {title && <h2 className="panel-title">{title}</h2>}
            {subtitle && <p className="panel-subtitle">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  )
}

/** A headline number. `tone` colours only the value, never the whole card — a card
 *  washed in red reads as broken rather than as "this much is unexplained". */
export function Stat({
  label,
  value,
  sub,
  tone = "neutral",
  hint,
}: {
  label: string
  value: string
  sub?: React.ReactNode
  tone?: "neutral" | "ok" | "warn" | "bad"
  hint?: string
}) {
  const toneClass = {
    neutral: "text-foreground",
    ok: "text-[hsl(var(--ok))]",
    warn: "text-[hsl(var(--warn))]",
    bad: "text-[hsl(var(--bad))]",
  }[tone]

  return (
    <div className="panel px-5 py-4" title={hint}>
      <p className="text-[11px] font-medium uppercase tracking-wider text-foreground/45">
        {label}
      </p>
      <p className={cn("tnum mt-2 text-[28px] font-semibold leading-none", toneClass)}>{value}</p>
      {sub && <div className="mt-2 text-xs leading-relaxed text-foreground/50">{sub}</div>}
    </div>
  )
}

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: React.ReactNode
  tone?: "neutral" | "ok" | "warn" | "bad" | "accent"
  className?: string
}) {
  const tones = {
    neutral: "border-border bg-foreground/[0.04] text-foreground/60",
    ok: "border-[hsl(var(--ok))]/30 bg-[hsl(var(--ok))]/10 text-[hsl(var(--ok))]",
    warn: "border-[hsl(var(--warn))]/30 bg-[hsl(var(--warn))]/10 text-[hsl(var(--warn))]",
    bad: "border-[hsl(var(--bad))]/30 bg-[hsl(var(--bad))]/10 text-[hsl(var(--bad))]",
    accent: "border-primary/30 bg-primary/10 text-primary",
  }[tone]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-0.5 text-[11px] font-medium",
        tones,
        className,
      )}
    >
      {children}
    </span>
  )
}

/** Exception category chip, coloured from the shared category palette so the same
 *  category is the same colour in the chart, the table and the findings list. */
export function CategoryChip({ category }: { category: string | null }) {
  const label = humanize(category)
  if (!category) return <Badge>{label}</Badge>
  const color = colorFor(category)
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-0.5 text-[11px] font-medium"
      style={{ borderColor: `${color}44`, backgroundColor: `${color}14`, color }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  )
}

export function SeverityDot({ classification }: { classification: string | null }) {
  const tone = severityOf(classification)
  const color = { critical: "--bad", warning: "--warn", info: "--ok" }[tone]
  return (
    <span
      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
      style={{ backgroundColor: `hsl(var(${color}))` }}
    />
  )
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn("font-mono text-[12px] tnum", className)}>{children}</span>
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-5 py-12 text-center text-sm text-foreground/40">{children}</div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 px-5 py-12 text-sm text-foreground/40">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-foreground/20 border-t-primary" />
      {label ?? "Loading…"}
    </div>
  )
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="m-5 rounded-lg border border-[hsl(var(--bad))]/25 bg-[hsl(var(--bad))]/[0.06] px-4 py-3">
      <p className="text-sm text-[hsl(var(--bad))]">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 rounded-md border border-border px-2.5 py-1 text-xs text-foreground/70 transition-colors hover:bg-foreground/5"
        >
          Retry
        </button>
      )}
    </div>
  )
}

/** Horizontal proportion bar used in the exception breakdown. */
export function Bar({ fraction, color }: { fraction: number; color: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-foreground/[0.06]">
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${Math.max(fraction * 100, 1.5)}%`, backgroundColor: color }}
      />
    </div>
  )
}
