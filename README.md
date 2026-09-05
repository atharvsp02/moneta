# Moneta

**Settlement intelligence agent.** Reconciles a merchant's own accounting books against
Razorpay's settlement data, explains every mismatch in plain language, and reports an
honest match rate — including what it could not resolve.

*Razorpay AI Buildathon 2026 — Track 04: AI Finance Controller*

> Named for Juno Moneta, the Roman goddess whose temple minted Rome's currency and gave us
> both "money" and "mint".

---

## The problem

When a merchant is paid through Razorpay, the money does not arrive order by order. Every
settlement cycle (~T+2), Razorpay sends **one net lump sum** covering hundreds of orders,
minus the MDR fee, minus 18% GST on that fee, minus any refunds netted out.

Someone in finance then has to reverse-engineer *"these 150 orders, less this fee, less
this tax, equals that one bank credit"* — and check that the company's own ledger says the
same thing. This is still substantially manual, and it is where money quietly goes missing.

Razorpay's **Optimizer** already reconciles settlements against the **bank**. The
RazorpayX–Tally integration handles **vendor payouts**. Neither covers the layer between:
settlement data versus the merchant's **sales ledger**, with the fee and GST breakdown
unpacked.

That layer is what Moneta does — and unlike a rules engine, it can explain itself when you
question a mismatch.

## What makes it different

**It can be interrogated.** Ask *"why doesn't order 4021 match?"* and it investigates —
pulling the payment, the vouchers, the fee breakdown, adjacent settlement cycles — then
answers with the evidence it found. Not a flag with a category code.

**It refuses to guess.** The agent is instructed that a plausible-sounding wrong
attribution is worse than an honest non-answer, because someone will act on it. Cases it
cannot attribute are reported as `UNRESOLVED`, with what it checked and what was missing.

**It never does the arithmetic.** Matching is exact integer comparison in paise, in Python.
The model is never asked whether two amounts are equal.

---

## Results

Measured on a **held-out dataset** the engine was never tuned against, generated from a
different seed. Reproduce with `moneta eval --name holdout`.

### Deterministic engine

| | dev (seed 7) | holdout (seed 91) |
|---|---|---|
| Match rate by value | 93.62% | **91.69%** |
| Match rate by record | 92.74% | **93.02%** |
| Records reconciled | 115 / 124 | **120 / 129** |
| Value reconciled | ₹428,539.23 of ₹457,722.91 | **₹474,995.52 of ₹518,056.74** |
| Runtime | 0.74 ms | **0.93 ms** (≈139,000 records/sec) |

### Exception attribution vs. injected ground truth (holdout, rules layer only)

```
injected errors        13
detected                9   (69.2%)
correctly attributed    9   (69.2%)
misclassified           0
missed entirely         4
unclaimed predictions   0   ← nothing flagged that was not actually wrong

micro precision    100.0%     micro recall  69.2%
```

| Classification | n | precision | recall | F1 |
|---|---|---|---|---|
| `AMOUNT_MISMATCH` | 3 | 100% | 100% | 100% |
| `DUPLICATE_BOOKING` | 2 | 100% | 100% | 100% |
| `MISSING_IN_BOOKS` | 2 | 100% | 100% | 100% |
| `MISSING_REFUND_IN_BOOKS` | 2 | 100% | 100% | 100% |
| `AGGREGATE_FEE_MISMATCH` | 2 | — | 0% | — |
| `GST_INPUT_ROUNDING_DRIFT` | 1 | — | 0% | — |
| `CROSS_CYCLE_REFUND` | 1 | — | 0% | — |

**Read the bottom three rows.** Those are the settlement-scope faults the deterministic
layer deliberately does *not* attribute — it quantifies the delta and hands the case to the
investigation agent. Scored with the agent disabled, they count as misses. This is the
honest deterministic floor, not the full-system number, and the table shows it rather than
hiding it.

**100% micro precision matters more than the recall figure.** Zero unclaimed predictions
means the engine never flagged something that was not actually wrong. For a tool that a
controller acts on, a false alarm costs more than a known gap.

---

## Quick start

```bash
# 1. Install
python -m venv .venv
.venv/Scripts/pip install -e .          # Windows;  .venv/bin/pip on macOS/Linux

# 2. Generate synthetic data (Razorpay settlement schema + Tally-style books)
moneta generate --name dev     --seed 7  --orders 120
moneta generate --name holdout --seed 91 --orders 120

# 3. Reconcile
moneta reconcile --name dev --no-agent   # deterministic pass only, no API key needed
moneta reconcile --name dev              # + LLM investigation of the residue

# 4. Score against ground truth
moneta eval --name holdout

# 5. Dashboard
moneta serve --name dev                  # API on :8000
cd web && pnpm install && pnpm dev       # UI on :3000
```

The investigation and Q&A layers need a Gemini API key. Put it in `.env` at the project
root:

```
GEMINI_API_KEY=your-key-here
```

**Without a key, everything deterministic still works.** The reconciliation runs, the match
rate is computed, exceptions are classified where the rules are unambiguous, and the report
states plainly that the agent did not run and which cases went uninvestigated. It degrades
to "here is what is wrong, unexplained" — never to a quietly shorter exception list.

---

## How it works

```
settlement CSV + books CSV
          │
          ▼
  ┌───────────────────────────┐
  │  1. DETERMINISTIC ENGINE  │   integer paise · no LLM · 0.74 ms
  │  order ↔ voucher matching │
  │  settlement reconstruction│
  │  clearing-account timing  │
  └─────────────┬─────────────┘
                │  cases it can quantify but not attribute
                ▼
  ┌───────────────────────────┐
  │  2. INVESTIGATION AGENT   │   Gemini + 8 read-only tools
  │  finds root cause, or     │   shaped like razorpay-mcp-server
  │  says UNRESOLVED          │
  └─────────────┬─────────────┘
                ▼
     audit trail · match rate · exception breakdown
     honest unresolved list · live Q&A
```

**Rules first, LLM second — and the boundary is enforced, not just intended.** The
deterministic layer decides every match; the LLM layer cannot change a match, a delta or a
total. A rules engine cannot hallucinate ₹4,000 into existence, and a model that never
touches the arithmetic cannot hallucinate it away.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the design in full: the layer seam, the tool
layer, the money representation, and why the agent is given two different tool sets.

### The fee/GST case, concretely

The single most common real mismatch: the merchant books the gateway fee at an assumed flat
rate, while Razorpay charges **per-method MDR — and UPI is zero-rated**. A book-heavy UPI
month produces a fee delta with no single order to blame it on.

The rules engine detects the delta and stops, because several causes fit. The agent calls
`compute_fee_breakdown`, which returns the true blended MDR in basis points, the per-method
split, *and* both `gst_computed_per_transaction` and `gst_if_computed_on_aggregate_fee`.
The difference between those two numbers is the rounding-drift diagnosis — computed in
Python and handed to the model as evidence, never derived by the model itself.

---

## Safety and scope

**Moneta detects, explains and flags. It never writes to the merchant's ledger.**

This is structural, not a policy the model is asked to follow. The tool registry contains
eleven functions and **all eleven are reads**. There is no write path to disable. The agent
cannot post a journal entry, adjust a voucher, or move money, because no such capability
was built. `recommended_action` on every finding is explicitly for a human to carry out.

The one API route that is not a read (`POST /api/investigate`) re-runs the agent over open
cases and writes to `out/` — never to the books.

### What this is not

- Not a bank ↔ Razorpay matcher — that is Optimizer, already shipped.
- Not vendor-payout reconciliation — that is the existing RazorpayX–Tally integration.
- Not an auto-correcting agent. It proposes; a human disposes.
- Not a chatbot over a spreadsheet. The matching is real, deterministic and correct first.

This is a **closing and verification loop**, not a collections or growth loop.

---

## Limitations

Stated plainly, because a reconciliation tool that oversells itself is worse than useless.

- **Data is synthetic.** It mirrors Razorpay's real settlement schema (`entity_id`, `type`,
  `debit`/`credit`, `fee`, `tax`, `settled_at`, `settlement_utr`, `order_id`, `method`) and
  a realistic Tally-style double-entry ledger, but it is generated, not production data.
  Real books are messier — free-text narrations, inconsistent order-id conventions,
  manual journal entries.
- **The rate card is fixed.** UPI at 0 bps, everything else at 200 bps. Real MDR varies by
  card network, issuer and negotiated merchant terms.
- **Order-id linkage is assumed.** Matching keys on `order_id` present on both sides. Real
  merchants frequently do not carry the gateway order id into the ledger at all — fuzzy
  linkage by amount, date and narration is a genuinely harder problem and is not solved
  here.
- **The agent is scored on seven injected fault classes.** Novel real-world causes outside
  those classes would most likely land as `UNRESOLVED` — which is the designed behaviour,
  but it is a gap, not a feature.
- **Single-currency, single-entity.** No multi-currency settlements, no cross-entity
  netting.
- **Held-out set is 13 injected faults over 124 records.** Enough to be honest about, not
  enough for tight confidence intervals.

---

## Audit trail

Every decision is logged to `out/<name>.audit.jsonl` as an append-only event stream: each
match decision with its reason, each exception with its evidence, each agent tool call with
its arguments and duration, each finding with its confidence. The dashboard's **Audit
trail** tab is a viewer over that file.

```
match_decision            order_id=… matched=false reason="gross amount differs"
exception_detected        key=… classification=AMOUNT_MISMATCH delta=₹7,740.00
investigation_started     case_key=… family=fee_and_gst
tool_call                 compute_fee_breakdown({"settlement_utr": "…"})
finding_recorded          classification=AGGREGATE_FEE_MISMATCH confidence=high
```

---

## Repository layout

```
src/moneta/
  money.py       paise arithmetic, MDR, GST, half-up rounding
  schema.py      settlement rows, vouchers, accounts, rate card
  generate.py    synthetic data + seven injected fault classes + labels
  load.py        CSV → dataclasses
  engine.py      the deterministic reconciliation pass
  tools.py       the agent's read-only view, shaped like razorpay-mcp-server
  agent.py       investigation agent + Q&A layer
  pipeline.py    load → reconcile → investigate → report, audited throughout
  audit.py       append-only JSONL event log
  eval.py        scoring against injected ground truth
  api.py         FastAPI surface
  cli.py         generate · reconcile · eval · serve · models
web/             Next.js dashboard
data/            generated datasets + ground-truth labels
out/             reports, findings, audit trails, eval results
```

## CLI

| Command | What it does |
|---|---|
| `moneta generate` | Build a synthetic settlement + books dataset with injected faults |
| `moneta reconcile` | Run the pipeline; `--no-agent` for the deterministic pass only |
| `moneta eval` | Reconcile and score against the dataset's ground-truth labels |
| `moneta serve` | Serve the reconciliation API for the dashboard |
| `moneta models` | List Gemini models available to your key |

---

## Web routes

| Route | What it is |
|---|---|
| `/` | Landing page — the problem, the approach, the held-out results, FAQ |
| `/dashboard` | The working dashboard: overview, exceptions, records, Ask Moneta, evaluation, audit trail |

The landing page shows live figures when the API is running and falls back to the
committed run artefacts when it is not, so a fresh clone still renders real numbers.
