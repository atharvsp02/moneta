"use client"

import { useEffect, useRef, useState } from "react"
import { ApiError, api, type ToolCall } from "@/lib/api"
import { Badge, Panel } from "./primitives"

type Turn = {
  role: "user" | "assistant"
  content: string
  toolCalls?: ToolCall[]
  meta?: { turns: number; ms: number; tokens: number }
  failed?: boolean
}

const SUGGESTIONS = [
  "What is the match rate for this run, and what is the unresolved value?",
  "Which exception is costing the most money, and why?",
  "Why doesn't the gateway fee in our books match what Razorpay charged?",
  "Show me every order that Razorpay settled but we never recorded.",
]

export function Ask({ agentAvailable }: { agentAvailable: boolean }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [turns, busy])

  async function send(question: string) {
    const text = question.trim()
    if (!text || busy) return
    setInput("")
    // Only completed exchanges go back as history; the pending question is the prompt.
    const history = turns
      .filter((t) => !t.failed)
      .map((t) => ({ role: t.role, content: t.content }))
    setTurns((prev) => [...prev, { role: "user", content: text }])
    setBusy(true)
    try {
      const res = await api.ask(text, history)
      setTurns((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.answer,
          toolCalls: res.tool_calls,
          meta: {
            turns: res.turns,
            ms: res.duration_ms,
            tokens: res.tokens.input + res.tokens.output,
          },
          failed: Boolean(res.error),
        },
      ])
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        {
          role: "assistant",
          content: err instanceof ApiError ? err.message : String(err),
          failed: true,
        },
      ])
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Ask Moneta"
      subtitle="Questions are answered from this run, using the same read-only tools. Every number quoted comes from a tool result."
      right={
        <Badge tone={agentAvailable ? "ok" : "warn"}>
          {agentAvailable ? "Agent online" : "No API key"}
        </Badge>
      }
      bodyClassName="flex h-[calc(100vh-19rem)] min-h-[26rem] flex-col"
    >
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
        {turns.length === 0 && (
          <div className="mx-auto max-w-2xl pt-6 text-center">
            <p className="text-sm text-foreground/55">
              Moneta can explain any part of this reconciliation. It reads the settlement data and
              the merchant&apos;s books through tools — it cannot edit either.
            </p>
            <div className="mt-5 grid gap-2 text-left sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  disabled={!agentAvailable || busy}
                  className="rounded-lg border border-border bg-foreground/[0.02] px-3.5 py-3 text-left text-[13px]
                             leading-relaxed text-foreground/65 transition-colors hover:border-primary/30
                             hover:bg-primary/[0.04] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[42rem] rounded-2xl rounded-br-md bg-primary/10 px-4 py-2.5 text-[13px] leading-relaxed text-foreground">
                {turn.content}
              </div>
            </div>
          ) : (
            <div key={i} className="max-w-[52rem] space-y-2">
              {turn.toolCalls && turn.toolCalls.length > 0 && <ToolTrace calls={turn.toolCalls} />}
              <div
                className={`rounded-2xl rounded-bl-md border px-4 py-3 text-[13px] leading-relaxed whitespace-pre-wrap ${
                  turn.failed
                    ? "border-[hsl(var(--bad))]/25 bg-[hsl(var(--bad))]/[0.06] text-[hsl(var(--bad))]"
                    : "border-border bg-foreground/[0.03] text-foreground/85"
                }`}
              >
                {turn.content}
              </div>
              {turn.meta && (
                <p className="pl-1 text-[11px] text-foreground/35">
                  {turn.meta.turns} model turns · {(turn.meta.ms / 1000).toFixed(1)}s ·{" "}
                  {turn.meta.tokens.toLocaleString()} tokens
                </p>
              )}
            </div>
          ),
        )}

        {busy && (
          <div className="flex items-center gap-2.5 text-[13px] text-foreground/45">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-foreground/20 border-t-primary" />
            Investigating — pulling evidence through tools…
          </div>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
        className="flex items-center gap-2 border-t border-border px-5 py-3.5"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={!agentAvailable || busy}
          placeholder={
            agentAvailable
              ? "Why doesn't order #… match?"
              : "Set GEMINI_API_KEY and restart the API to enable Q&A"
          }
          className="flex-1 rounded-lg border border-border bg-foreground/[0.03] px-3.5 py-2.5 text-[13px]
                     text-foreground placeholder:text-foreground/30 focus:border-primary/40 focus:outline-none
                     disabled:cursor-not-allowed disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!agentAvailable || busy || !input.trim()}
          className="rounded-lg bg-primary px-4 py-2.5 text-[13px] font-medium text-primary-foreground
                     transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
        >
          Ask
        </button>
      </form>
    </Panel>
  )
}

/** Shows what the agent actually looked up before answering. This is the difference
 *  between a grounded answer and a plausible one, so it is shown, not hidden. */
function ToolTrace({ calls }: { calls: ToolCall[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-border bg-background/40">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-foreground/45 transition-colors hover:text-foreground/70"
      >
        <span className={`transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
        Evidence gathered — {calls.length} tool {calls.length === 1 ? "call" : "calls"}
        <span className="ml-auto flex flex-wrap gap-1">
          {!open &&
            calls.slice(0, 4).map((c, i) => (
              <span key={i} className="rounded bg-foreground/[0.06] px-1.5 py-0.5 font-mono text-[10px]">
                {c.tool}
              </span>
            ))}
        </span>
      </button>
      {open && (
        <ul className="space-y-2 border-t border-border px-3 py-2.5">
          {calls.map((c, i) => (
            <li key={i}>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-primary/80">{c.tool}</span>
                <span className="font-mono text-[10px] text-foreground/35">
                  {JSON.stringify(c.arguments)}
                </span>
                {!c.ok && <Badge tone="bad">failed</Badge>}
                <span className="ml-auto text-[10px] text-foreground/30">
                  {c.duration_ms.toFixed(1)}ms
                </span>
              </div>
              <p className="mt-0.5 overflow-x-auto font-mono text-[10px] leading-relaxed text-foreground/35">
                {c.result_summary}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
