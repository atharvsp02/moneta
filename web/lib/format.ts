/** Presentation helpers. All amounts arrive pre-formatted from the API in paise-exact
 *  rupee strings; nothing here re-derives a money value from a number. */

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "n/a"
  return `${(value * 100).toFixed(digits)}%`
}

/** Turns MISSING_REFUND_IN_BOOKS into "Missing refund in books". */
export function humanize(label: string | null | undefined): string {
  if (!label) return "Unclassified"
  const words = label.replace(/_/g, " ").toLowerCase()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function timeAgo(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const seconds = Math.floor((Date.now() - then) / 1000)
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return new Date(iso).toLocaleDateString()
}

export function clockTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleTimeString(undefined, { hour12: false })
}

export function shortDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { day: "2-digit", month: "short" })
}

/** Truncates long synthetic ids for table cells without losing the distinctive tail. */
export function shortId(value: string, head = 10, tail = 6): string {
  if (value.length <= head + tail + 1) return value
  return `${value.slice(0, head)}…${value.slice(-tail)}`
}

const SEVERITY: Record<string, "critical" | "warning" | "info"> = {
  MISSING_IN_BOOKS: "critical",
  MISSING_IN_SETTLEMENT: "critical",
  DUPLICATE_BOOKING: "critical",
  AMOUNT_MISMATCH: "critical",
  MISSING_REFUND_IN_BOOKS: "warning",
  REFUND_AMOUNT_MISMATCH: "warning",
  AGGREGATE_FEE_MISMATCH: "warning",
  SETTLEMENT_NET_MISMATCH: "warning",
  CROSS_CYCLE_REFUND: "info",
  GST_INPUT_ROUNDING_DRIFT: "info",
  UNRESOLVED: "critical",
}

export function severityOf(classification: string | null | undefined) {
  return SEVERITY[classification ?? ""] ?? "info"
}

/** Stable colours for the exception breakdown chart, ordered by severity so the
 *  eye reads the worst categories first. */
export const CATEGORY_COLORS: Record<string, string> = {
  MISSING_IN_BOOKS: "#f87171",
  MISSING_IN_SETTLEMENT: "#fb923c",
  DUPLICATE_BOOKING: "#f472b6",
  AMOUNT_MISMATCH: "#facc15",
  MISSING_REFUND_IN_BOOKS: "#a78bfa",
  REFUND_AMOUNT_MISMATCH: "#818cf8",
  AGGREGATE_FEE_MISMATCH: "#38bdf8",
  SETTLEMENT_NET_MISMATCH: "#2dd4bf",
  CROSS_CYCLE_REFUND: "#78fcd6",
  GST_INPUT_ROUNDING_DRIFT: "#4ade80",
  OPEN_FOR_INVESTIGATION: "#94a3b8",
  UNRESOLVED: "#ef4444",
}

export function colorFor(category: string): string {
  return CATEGORY_COLORS[category] ?? "#64748b"
}
