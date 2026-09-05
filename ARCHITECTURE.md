# Architecture

## The problem, precisely

A Razorpay settlement is not a payment. It is one net bank credit that stands for many
payments, with three deductions folded in:

```
net bank credit  =  Σ gross payments
                 −  Σ MDR fee          (per-method: UPI 0 bps, card/netbanking/wallet 200 bps)
                 −  Σ GST on that fee  (18%, rounded per transaction)
                 −  Σ refunds netted   (possibly issued in an earlier cycle)
```

The merchant's books record the same economic events, but in a different shape: a sales
voucher per order, a credit note per refund, and one receipt voucher per settlement that
splits the credit across Bank, Payment Gateway Charges and GST Input Credit.

Reconciliation means proving those two shapes describe the same money — and, when they
don't, saying exactly why.

## Two layers, and why the split matters

```
                  settlement CSV            books CSV
                  (Razorpay schema)         (Tally-style vouchers)
                         │                        │
                         └──────────┬─────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  LAYER 1 — deterministic       │   engine.py
                    │  integer paise, no LLM         │
                    │                                │
                    │  · order ↔ voucher matching    │
                    │  · refund ↔ credit note        │
                    │  · settlement net reconstruct  │
                    │  · clearing account timing     │
                    └───────────────┬───────────────┘
                                    │
                 ┌──────────────────┴──────────────────┐
                 ▼                                     ▼
      classification is set                  classification is None
      (cause is unambiguous)                 (delta quantified, cause unknown)
                 │                                     │
                 │                                     ▼
                 │                     ┌───────────────────────────────┐
                 │                     │  LAYER 2 — investigation agent │  agent.py
                 │                     │  Gemini + 8 read-only tools    │
                 │                     │  submits a finding, or says    │
                 │                     │  UNRESOLVED                    │
                 │                     └───────────────┬───────────────┘
                 └──────────────────┬──────────────────┘
                                    ▼
                    audit.py ── every decision, JSONL
                    eval.py  ── scored against injected ground truth
                    api.py   ── served to the dashboard
                    agent.py ── Q&A: "why doesn't order X match?"
```

**The deterministic layer decides every match.** It never asks the model whether two
amounts are equal. Numeric matching is exact integer comparison in paise, and a
discrepancy is a difference the code computed, not a difference the model perceived.

**The LLM layer never decides a match.** It receives cases the rules engine has already
quantified but cannot attribute, and its only job is to find the root cause. It cannot
change a match, a delta or a total.

This split is the whole design. It is what makes a match rate from this system mean
something: a rules engine cannot hallucinate ₹4,000 into existence, and a model that
never touches the arithmetic cannot hallucinate it away.

## Why the engine leaves some cases open

`Discrepancy.classification` is the seam between the layers.

The rules engine sets a classification when the cause is structurally unambiguous — a
settled payment with no sales voucher can only be `MISSING_IN_BOOKS`; two sales vouchers
against one payment can only be `DUPLICATE_BOOKING`. There is nothing to investigate.

It leaves `classification = None` when it can measure the delta but several causes would
produce the same symptom. A settlement whose booked gateway fee differs from the sum of
per-order fees might be a flat-rate assumption, a per-transaction GST rounding drift, a
refund that landed in a neighbouring cycle, or something else. Distinguishing those needs
evidence pulled from several places and a judgement about which story the evidence tells.
That is the agent's work.

`build_cases()` groups open discrepancies by `(key, scope, symptom family)` so that a fee
delta and a GST delta on the same settlement are investigated as one case rather than two
— they usually share a root cause, and splitting them would double-count it.

## Money

All amounts are integers in paise. There is no float arithmetic anywhere in the
reconciliation path. `money.py` provides the only three operations that create money:

- `bps_of(amount, bps)` — MDR at a basis-point rate
- `gst_on(fee)` — 18% GST
- `round_half_up` — the rounding both use

Both quantise through `Decimal` with `ROUND_HALF_UP`, which is what a payment processor
does and what Python's built-in `round()` does not (it rounds half to even). Getting this
wrong produces exactly the one-paise drifts this system is meant to explain, so it is not
a detail.

## The tool layer

The agent has no access to the dataset except through `tools.py`. Every tool is a pure
read over data already in memory, returns JSON, and formats money as both `paise` (exact)
and `formatted` (for quoting).

The tools are shaped to mirror `razorpay-mcp-server`'s call signatures — `fetch_payment`,
`fetch_settlement`, `fetch_refunds_for_order` — so that swapping the synthetic backend for
the real MCP server is a change of implementation, not of architecture.

Two tools deserve specific mention:

- **`compute_fee_breakdown`** exists so the model never computes a fee, a blended rate or
  a GST total itself. It returns the per-method split, the true blended MDR in basis
  points, and — critically — both `gst_computed_per_transaction` and
  `gst_if_computed_on_aggregate_fee`. The difference between those two numbers *is* the
  rounding-drift diagnosis, computed in Python and handed over as evidence.
- **`search_settlement_entries_by_amount`** lets the agent test the cross-cycle refund
  hypothesis by finding where an unexplained value actually went, instead of speculating
  that it went somewhere.

### Two tool sets, deliberately

`TOOL_DEFINITIONS` reads the raw data. `RECON_TOOL_DEFINITIONS` reads the finished
reconciliation (`get_reconciliation_summary`, `lookup_order_status`, `list_exceptions`).

The **investigation agent gets only the first set**. It is asked to attribute a cause from
primary evidence; handing it the engine's own verdict would invite it to restate that
verdict instead of investigating, and the eval would then be scoring an echo.

The **Q&A layer gets both**, because it is answering questions *about* a completed run and
the run's conclusions are legitimately part of the answer.

## Bounding

There is no write path. Not "there is a write path that is disabled" — the tool registry
contains eleven functions and all eleven are reads. The agent cannot post a journal entry,
adjust a voucher, or move money, because no such capability was built.

`submit_finding` is the agent's only structured output, and its `recommended_action` field
is explicitly described to the model as something a human carries out.

The API surface has one route that is not a read (`POST /api/investigate`), and it re-runs
the agent over open cases — it writes to `out/`, never to the books.

## Honest accounting of the residue

Three numbers exist specifically to stop the match rate from flattering itself:

1. **Unsettled timing balance.** Payments captured but not settled at the T+2 cutoff are
   reported separately as timing differences, not counted as matched and not counted as
   exceptions. They are neither.
2. **Clearing account check.** `Razorpay Clearing` should hold exactly the unsettled
   payments minus unsettled refunds. The engine computes the expected balance, compares it
   to the actual, and reports `unexplained_paise`. This is a global consistency check that
   catches value the per-order matching missed.
3. **`could_not_resolve`.** Findings classified `UNRESOLVED` are surfaced in the report,
   in the API and at the top level of the dashboard. The agent is instructed that a
   plausible-sounding wrong attribution is worse than an honest non-answer, because
   someone acts on it.

## Evaluation

The generator records every fault it injects — key, type, rupee impact, which side of the
books it landed on — into `<name>.labels.json`. `eval.py` replays a run against that file.

Matching labels to predictions runs in three passes, and no prediction can be claimed
twice:

1. **key + class** — the unambiguous case.
2. **key only** — detected, but attributed to the wrong cause. Scored as a
   misclassification, and as a false positive for the class that was wrongly claimed.
3. **class only** — cross-scope. A cross-cycle refund is injected against an `order_id`
   but surfaces as a bank-credit delta on a `settlement_utr`; a strict key join would
   score a correct attribution as a miss.

Unclaimed predictions are counted as false positives. This is the number that matters
most: it says how often Moneta flagged something that was not actually wrong.

The harness reports detection recall (did we catch it at all) separately from attribution
accuracy (did we say the right thing about it), because those are different failures with
different costs.

## Module map

| Module | Responsibility |
|---|---|
| `money.py` | Paise arithmetic, MDR, GST, half-up rounding. The only place money is created. |
| `schema.py` | Settlement rows, vouchers, ledger lines, accounts, the MDR rate card. |
| `generate.py` | Synthetic settlements + books, with seven injected fault classes and a label file. |
| `load.py` | CSV → dataclasses; derives settlement cycles from their constituent rows. |
| `engine.py` | The deterministic pass. Every match decision and every delta. |
| `tools.py` | The agent's read-only view of the world, shaped like `razorpay-mcp-server`. |
| `agent.py` | Investigation agent, Q&A layer, shared Gemini transport with rate limiting. |
| `pipeline.py` | Orchestration: load → reconcile → investigate → report, auditing each step. |
| `audit.py` | Append-only JSONL event log. |
| `eval.py` | Scoring against injected ground truth. |
| `api.py` | FastAPI surface for the dashboard. |
| `cli.py` | `generate`, `reconcile`, `eval`, `serve`, `models`. |
| `web/` | Next.js dashboard: overview, exceptions, records, Ask, evaluation, audit. |

## Failure handling

The agent's failure modes are enumerated rather than caught generically:

- Gemini `finish_reason` values that terminate a turn without a tool call
  (`MALFORMED_FUNCTION_CALL`, `MAX_TOKENS`, safety blocks) each map to a specific message
  in the finding, and the case is reported unresolved rather than dropped.
- 429s back off exponentially and are counted per finding (`rate_limit_waits`), so a slow
  run is distinguishable from a stuck one.
- A `submit_finding` call missing required fields is rejected back to the model as a tool
  error rather than being partially accepted.
- Hitting `MAX_TURNS` produces an explicit unresolved finding, never a guess.
- With no API key at all, the deterministic engine runs unchanged and the report states
  plainly that the agent did not run and which cases went uninvestigated.

In every one of these, the deterministic delta still stands and is still reported. The
system degrades to "here is what is wrong, unexplained" — never to a silently smaller
exception list.
