# Demo script — 5 minutes

The whole pitch is built around one case, and the case is chosen deliberately: a
cross-cycle refund that the deterministic engine **cannot** solve, that surfaces as two
unexplained deltas on two different settlements, and that only makes sense when something
connects them.

Do not wing this. The 30 seconds where Moneta connects those two numbers live is the most
valuable half-minute in the pitch.

---

## Setup (before recording)

```bash
moneta reconcile --name dev          # produces findings + audit trail
moneta serve --name dev              # API on :8000
cd web && pnpm dev                   # UI on :3000
```

Have `.env` with `GEMINI_API_KEY` set. Open `http://localhost:3000` on the **Overview** tab.
Have a terminal visible for the CLI moment in section 4.

---

## 0:00 — 0:40 · The problem

> "When you get paid through Razorpay, the money doesn't arrive order by order. Every two
> days you get **one lump sum** covering hundreds of orders — minus the gateway fee, minus
> 18% GST on that fee, minus any refunds netted out.
>
> Someone in finance has to work backwards from that single bank credit to prove it matches
> what the books say. That's still substantially manual.
>
> Razorpay's Optimizer already reconciles settlements against the **bank**. Moneta is the
> layer above: settlements against the merchant's **own ledger** — and unlike a rules
> engine, you can ask it why."

---

## 0:40 — 1:30 · The honest numbers

Stay on **Overview**. Point at the four stat cards in order.

> "124 records, 19 settlement cycles. **93.6% of value reconciled** in 0.74 milliseconds —
> that's the deterministic engine, no LLM anywhere in the matching path.
>
> And this number here — **₹29,183 unresolved**. I want to be direct about that, because a
> reconciliation tool that shows you 100% is a tool that's hiding something."

Scroll to the exception breakdown.

> "Every one of those exceptions is categorised and costed. Duplicate bookings, amount
> mismatches, missing entries, missing refunds."

Scroll to **Could not resolve**.

> "And this panel is here on purpose. It renders even when it's empty. If Moneta can't
> explain something, it says so on the front page — not in a log file."

---

## 1:30 — 2:15 · Rules first, LLM second

Switch to **Exceptions**. Click a `DUPLICATE_BOOKING` row to expand it.

> "The rules engine closed this one itself. Two sales vouchers, one payment — there's no
> ambiguity, so there's nothing to investigate. It's classified deterministically and the
> evidence is right here.
>
> The engine never asks a model whether two amounts are equal. Matching is integer
> comparison in paise. A rules engine can't hallucinate ₹4,000 into existence."

Now filter to the **open** cases and point at these two rows:

| Key | Delta | Rule |
|---|---|---|
| `079593718587` | **−₹513.17** | `booked_bank_credit_not_equal_to_reconstructed_settlement_net` |
| `594996572458` | **+₹513.17** | `booked_bank_credit_not_equal_to_reconstructed_settlement_net` |

> "But these two the engine could not close. It knows the *amount* is wrong on both — it
> just can't say *why*. Two different settlements, two days apart. Same number, opposite
> signs.
>
> A rules engine sees two problems. It has no way to know they're one."

---

## 2:15 — 3:30 · **The moment** — ask it live

Switch to **Ask Moneta**. Type this, live, do not paste a canned question:

```
Settlement 079593718587 is short by ₹513.17 and 594996572458 is over
by the same amount. Are these related?
```

While it runs, narrate the tool trace as it appears:

> "Watch what it's actually doing — it's not reasoning from memory. `fetch_settlement`,
> `fetch_settlement_entries`, `search_settlement_entries_by_amount`. Every number it's
> about to quote comes from a tool result, not from the model."

When the answer lands, expand the tool trace.

> "It found the refund. The books recorded the credit note on the day the refund was
> **issued**; Razorpay netted it out of the **following** settlement cycle. One event,
> booked in two different periods — which is exactly why it shows up as two equal and
> opposite deltas.
>
> That is a real reconciliation problem, and this is the part a rules engine structurally
> cannot do: it had to go *look* for where the money went."

**Then say the important sentence:**

> "And notice what it recommends — a journal entry for a **human** to post. Moneta has
> eleven tools and all eleven are reads. There's no write path to disable. It cannot touch
> the ledger, by construction."

---

## 3:30 — 4:15 · Proving it, not just showing it

Switch to **Evaluation**.

> "A demo is one case. This is the whole held-out set — different seed, data the engine was
> never tuned on. The generator knows exactly which faults it injected, so we can score
> against ground truth.
>
> **100% micro precision.** Zero unclaimed predictions — it never flagged something that
> wasn't actually wrong. For a tool a controller acts on, a false alarm costs more than a
> known gap."

Point at the per-class table, specifically the zero-recall rows.

> "And I'm showing you the rows it misses, not just the ones it gets. This panel lists every
> miss by name and rupee impact. A held-out score with the failures removed isn't a score."

---

## 4:15 — 4:45 · Audit trail

Switch to **Audit trail**. Filter to `tool_call`.

> "Every decision is on the record — every match with its reason, every exception with its
> evidence, every tool call with its arguments. Append-only JSONL. If a finance team is
> going to trust this, they have to be able to reconstruct exactly how it reached a
> conclusion."

---

## 4:45 — 5:00 · Close

> "Razorpay's Optimizer reconciles settlements against the bank. Moneta reconciles them
> against the books — hours of manual work per cycle, done in under a second, with a full
> audit trail and complete honesty about what it couldn't resolve.
>
> It's a closing and verification loop. It explains, it flags, and it never moves money."

---

## Handling the one thing that can go wrong

If the API rate-limits mid-demo, the Ask panel shows the real error and the reconciliation
numbers stay on screen. **Say so out loud** — it is a better moment than a smooth one:

> "That's the model API rate-limiting, and notice the reconciliation numbers didn't move.
> The deterministic layer doesn't depend on the model at all. Worst case, you get 'here's
> what's wrong, unexplained' — you never get a quietly shorter exception list."

Have a fallback: the same cross-cycle case already investigated is visible in
**Exceptions** → expand the row → the agent finding, evidence and tool calls are all
rendered from `out/dev.findings.json`.

---

## Questions to be ready for

**"Isn't this just Optimizer?"**
No. Optimizer matches settlements to the **bank statement**, across gateways. Moneta
matches settlements to the **sales ledger**, with the MDR and GST breakdown unpacked. The
RazorpayX–Tally integration is the accounts-*payable* side. This is the gap between them.

**"How do you know the LLM isn't making up the numbers?"**
It structurally cannot affect them. Matching and every delta are computed in Python in
integer paise before the model is invoked. The model receives cases that are already
quantified. Its output is a *classification and an explanation* — it cannot change a match,
a delta or a total. And `compute_fee_breakdown` exists specifically so it never has to do
arithmetic to reach a conclusion.

**"What if it's wrong?"**
Then it should say `UNRESOLVED`, and it's instructed that a plausible wrong answer is worse
than an honest non-answer. The eval measures exactly this: unclaimed predictions are
counted as false positives, and that number is currently zero.

**"Would you let it fix the ledger?"**
Not as built, and that's deliberate. Confidence isn't high enough for autonomous writes on
financial records, and the audit story is much stronger when a human is the actor. The
right next step is a *proposed* journal entry a controller approves — still a human in the
loop.
