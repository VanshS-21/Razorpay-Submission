# Glossary

Plain-language definitions of every term this project depends on. Written for
someone who has never worked in payments.

---

## The money path

**PSP — payment service provider.** The middleman between a customer and a
shop. Razorpay is one; so are Cashfree, PhonePe and Stripe. A shop with a plain
UPI ID can take money directly and receive the full amount. The moment it wants
cards, netbanking, EMI, refunds or a dashboard, it needs a PSP — and a PSP takes
a cut.

**Fee.** The PSP's cut. In this project, 2% of the sale.

**GST on the fee.** Tax at 18%, charged *on the fee* — not on the sale. On a
₹500 order the fee is ₹10.00 and the GST is ₹1.80 (18% of ₹10, not of ₹500).

**Net.** What actually reaches the shop, after fee and tax:

```
  ₹500.00   sale
- ₹ 10.00   fee (2%)
- ₹  1.80   GST on the fee (18% of the fee)
──────────
= ₹488.20   net
```

**Settlement.** One lump bank transfer covering many orders at once. Razorpay
collects all day, waits a day or two, then pays the shop **once** for the whole
batch. Eight ₹500 orders arrive as a single credit of ₹3,905.60 — not as eight
credits.

*This is the fact the entire project is built on.*

**UTR — unique transaction reference.** The reference number a bank puts on a
transfer, so it can be traced. When it is missing or garbled, a credit cannot be
joined to a payout by reference and has to be identified some other way.

---

## The three sources

Reconciliation needs three documents, each written by a different party:

| Document | Says what | Written by |
|---|---|---|
| **Order ledger** | what the shop sold | the shop |
| **Settlement report** | what the PSP paid, and what it deducted | the PSP |
| **Bank statement** | what actually landed in the account | the bank |

**Reconciliation.** Checking whether documents agree. **Three-way
reconciliation** is checking all three against each other.

Why three and not two? **Because two sources can agree with each other and both
be wrong.** Razorpay and the bank can be in perfect agreement about a number
that has nothing to do with what was actually sold. Only the order ledger knows
that.

---

## What one settlement goes through

Five stages, one job each. This is the answer to *"what happens, step by step,
to one settlement?"*

| | stage | job |
|---|---|---|
| 1 | **Ingest** | many CSV rows → one unit; money to paise; compute `expected_net` |
| 2 | **Match** | find the bank money → `observed_net` |
| 3 | **Classify** | judge it → **disposition + reason code** |
| 4 | **Score** | compare the verdict to the answer key |
| 5 | **Report** | console, `run.json`, `report.html` |

**Reconciliation unit.** One settlement and all its lines — the thing the engine
makes a single decision about. One settlement is many rows in the CSV, one per
payment, refund or dispute.

**`expected_net`.** What the bank *should* have credited, worked out from
Razorpay's side alone: add up `credit − debit` across every line.

**`observed_net`.** What the bank statement says actually moved.

**`delta`.** `observed_net − expected_net`. **Zero is the goal.** Every verdict
in the engine is, underneath, a decision about what a non-zero delta *means*.

**A zero delta does not mean nothing happened.** A chargeback settlement ties
perfectly, because Razorpay's report already accounted for the clawback — but
real money was taken back and a human needs to know. Hence two outputs, not one.

**Disposition.** Does a human need to look? → `reconciled` or `exception`.

**Reason code.** *Why* did the money move that way? → `clean`,
`chargeback_deduction`, `true_mismatch`, `ledger_mismatch`, and so on.

---

## Matching — the hard part

**Matching is not reconciling.** Matching answers *"which bank money belongs to
this settlement?"* and has no opinion on whether anything is correct.
Classifying answers *"is it right, and if not, why?"* A settlement can match
perfectly on its UTR and still be an exception.

Three passes, cheapest and most certain first:

| pass | method | evidence |
|---|---|---|
| **1** | join on UTR | the reference matches |
| **2** | exact amount + date window | the money matches, to the paise |
| **3** | subset-sum | a *combination* of payouts matches |

**`DATE_WINDOW_DAYS` = 3.** How late a credit may land and still count as the
same payout. NEFT settles same-day or next working day; a weekend plus a bank
holiday is three days.

**Subset-sum.** Given a target number and a list of numbers, find which
*combination* adds up to the target. A search, not a lookup.

It is the core problem here, because a bank credit is not *for* one payout — it
covers some **set** of payouts. Given five unpaid payouts of ₹100, ₹250, ₹400,
₹550 and ₹700, a credit of ₹950 could be ₹400 + ₹550 **or** ₹250 + ₹700.
Arithmetic alone cannot tell you which.

**The rule this project applies:** if more than one combination fits, the answer
is *no answer*. Two valid subsets means arithmetic found a coincidence, not an
identity — so the engine refuses to decide and escalates. Guessing would mark
the wrong payouts as paid, and nothing would ever announce the error.

**Why not match on a fragment of the UTR?** A bank often prints
`NEFT CR-2773XXXX-...`, and pulling `2773` out and matching it is tempting. But
at only 126 settlements two UTRs already share their first four characters, every
bank truncates differently, and — most importantly — *a matching prefix proves
two strings share characters; it does not prove the money is right.* That is a
language task, so it is the model's job, and the model only ever suggests.

**Orphan bank row.** A statement line the engine could not attach to any payout.
Often unrelated traffic — payroll, another PSP, a vendor payment — but reported
rather than discarded, so nothing leaves the account unseen.

---

## Measuring it

**False clear.** The engine marked something reconciled that actually needed a
human. Money quietly lost, with nothing flagged.

**False escalate.** The engine sent something to a human that was actually fine.
Costs ten minutes of review.

**These are not equally bad.** That asymmetry is the whole design. It is why the
report puts the false-clear rate first, above every flattering number.

**Ground-truth key.** The answer sheet the data generator writes alongside the
fake data, recording what each settlement's real problem was. Without it,
"84.9% accurate" would be a number with nothing behind it.

**Scoring** does not check "did everything reconcile." It checks **"was the
engine right?"** — comparing each verdict against the key.

**Holdout.** A second, harder dataset built to attack the engine, kept separate
from the one it was developed against. Every settlement in it carries *two*
defects at once.

**Mutation testing.** Deliberately breaking a constant or a rule to see whether
any test notices. A test that still passes after you break the thing it covers
was never testing that thing.

---

## Storing money

**Paise.** 1/100th of a rupee. All money in this project is stored as a **whole
number of paise** — ₹488.20 is the integer `48820`.

**The decimal only exists at the moment of display.** `rupees()` takes an
integer and returns a *string*. That decimal point is never a number, so it can
never be added to anything, so it can never drift.

**Why never decimals.** Computers store numbers in binary, and 1/10 has no exact
binary form (the way 1/3 has no exact decimal form). So `0.1 + 0.2` gives
`0.30000000000000004`. Try it:

```bash
python -c "print(0.1 + 0.2)"
```

This matters here more than in most software, because the matching is **exact**.
A drift of 0.0000000004 makes an exact match fail, and a perfectly good payout
gets escalated for no reason.

The tempting fix — forgive differences under ₹1 — is a trap. It builds a hiding
place: someone skimming 50 paise per transaction becomes invisible, because you
can no longer tell a rounding artefact from theft. Whole numbers are exact, so
any difference that shows up came from somewhere real.

**Basis points (bps).** 1/100th of a percent, used so that rates are integers
too. 2% is `FEE_BPS = 200`; 18% is `GST_BPS = 1800`. Writing 2% as `0.02` would
put a float back into the money path.

**`ROUNDING_TOLERANCE_PAISE` = 5.** Differences of five paise or less are
absorbed as fee/GST rounding drift, because Razorpay rounds each line's fee
separately and the rounded parts do not always sum to the rounded whole. Six
paise escalates. The narrowness is the point — a wide tolerance is a hiding
place.

---

## The model layer

**Deterministic vs probabilistic.** Deterministic code gives the same answer
every time and can be checked. A language model produces *plausible* output —
fine for a sentence, fatal for a rupee figure someone will quote to their bank.
So: **deterministic where money is concerned, probabilistic only where language
is.**

**Provider / backend.** The vendor answering — Anthropic or Gemini. Each sits
behind one small adapter class exposing a single method, so everything above it
(the prompts, the guard, the narration) never knows which company replied.
Switching with `--provider` must not move a single verdict; that is the point of
the boundary, and it is checkable by diffing two runs.

**Structured output.** Asking the model to reply in a fixed JSON shape rather
than free prose, so the answer can be *parsed* rather than *read*. The reply
still arrives as a string and has to be parsed.

**Narration.** The model's one genuine job: writing the human-readable note
attached to an exception.

**Lead.** A model-suggested identity for an unmatched bank row. Attached to the
exception as a suggestion for the human — **never** used to clear a payout. The
model proposes; arithmetic disposes.

**Guard.** The layer that re-checks everything the model produces. It extracts
every rupee figure from generated prose and rejects the whole note if even one
figure was not computed by the engine.

*What it is not:* it verifies that every figure is **real**, not that the
sentence is **true**. A real figure used in the wrong role passes.

**Guard rejection rate.** How often the model had to be overruled. Published,
because it is a fact about the system worth knowing.

**Thinking tokens.** Reasoning a model generates but does not show you. Invisible
in the reply, absent from `output_tokens`, and **billed at the output rate**. In
this project's one measured run (two calls against `gemini-3.5-flash`) they were
**87% of the output tokens and 83% of the bill** — counting only `output_tokens`
understated that run by 5.8×. The two figures are different things: 87% is
thinking's share of what was *generated*, 83% its share of what was *charged*,
because input tokens are also on the invoice and are billed at a lower rate.

**Rate limit / quota.** How many requests an API key may make. Gemini's free
tier allows 20 requests **per day** for `gemini-3.7-flash` and 5 for
`gemini-3.5-flash`. A 126-settlement batch needs 19 model calls — so the layer
barely fits in a free tier, while the deterministic engine needs no quota at all
and finishes in 0.04 seconds.

**Cost per 100 records.** Reported only when the whole batch was actually
processed. A run capped by `--narrate-limit` pays for a few notes, and dividing
that across every settlement understates the real cost — by 9.5× in the run that
exposed it.
