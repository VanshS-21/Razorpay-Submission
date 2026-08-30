# Three-way settlement reconciliation

**Razorpay AI Buildathon — Track 04, AI Finance Controller**

An agent that closes the settlement reconciliation loop across a batch of
synthetic data, reports its match rate, and produces a defensible exception list
for everything it could not explain.

```bash
python demo.py
```

No API key. No dependencies. Python 3.11+ and the standard library.

---

## The numbers

Full results, per-class breakdown, and caveats: [`eval/metrics.md`](eval/metrics.md).

| | main set | adversarial holdout |
|---|---|---|
| Settlements | 126 | 24 |
| **False-clear rate** | **0.0%** (0/19) | **0.0%** (0/12) |
| False-escalate rate | 0.0% | 0.0% |
| Match rate | 84.9% | 50.0% |
| Reason-code accuracy | 100.0% | not scored |
| Throughput | 1,179 lines in 0.025s — **~46,000 lines/sec** | |

72 tests. No API key or network required for any of them.

**Read the false-clear rate first.** It is the count of settlements the engine
called reconciled that the answer key says needed a human. In a finance system
that is the expensive error: a missed exception is money quietly lost, while an
unnecessary exception costs only review time. The two are not symmetric, and
reporting the flattering number first would hide the one that matters.

**The 100% on the main set is not a result, and is reported as a regression
guard only.** I wrote the defect generator and I wrote the detection rules, so
that number measures whether two expressions of the same assumptions agree with
each other. They do. That proves internal consistency, not capability. The
number worth reading is the holdout — see [Honest measurement](#honest-measurement).

---

## The problem

A merchant has three sources that should agree and never quite do:

| source | what it says |
|---|---|
| `settlement_recon.csv` | what Razorpay says it paid out, line by line |
| `bank_statement.csv` | what actually landed in the bank account |
| `order_ledger.csv` | what the business thinks it sold |

The bank does not credit per payment. It credits **one net lump sum per
transfer**, covering many payments *minus* fees, GST, refunds, chargebacks and
adjustments — and it may sweep several payouts into a single NEFT while quoting
only one UTR.

So "which settlement is this credit for?" has no answer in general. The right
question is "which **subset** of settlements does this credit cover?", and that
is a search, not a lookup. This is why reconciliation is still done by hand at
most merchants, and it is the thing the engine actually does.

---

## Architecture

> **Deterministic where money is concerned. Probabilistic only where language is.**

This is the central design decision and the one I would defend hardest.

| layer | implementation | why |
|---|---|---|
| Exact UTR join | integer arithmetic | most of the volume, zero ambiguity |
| Amount + date fallback | integer arithmetic | for unusable reference fields |
| Subset-sum decomposition | exhaustive, bounded, **exact** | consolidated transfers |
| Ledger + refund verification | integer arithmetic | the third source, made load-bearing |
| Fee / GST / dispute identities | integer paise | the ledger identity must hold |
| **Bank narration → identity** | **language model** | genuinely fuzzy text |
| **Exception note for a human** | **language model** | genuinely generative |
| Guard | deterministic re-verification | nothing from a model is trusted |

**Money is integer paise everywhere. No floats, anywhere in the money path.**
`0.1 + 0.2 != 0.3`, and an engine that cannot decide equality cannot reconcile.

### Where I deliberately did not use a model

The obvious build is to hand the whole ledger to a model and ask it to
reconcile. I did not, for a reason that is measurable rather than aesthetic:
such a system cannot report an honest match rate, because it cannot tell you
*why* it was right. Every figure this engine produces traces to an arithmetic
identity a human can re-derive.

Concretely, the model is **not** allowed to:

- compute, round, or estimate any monetary amount
- decide whether a settlement is reconciled or escalated
- assign a reason code
- assert a match — it may only *propose* one, which is then re-checked against
  the same exact-amount and date-window test the deterministic matcher uses

**The model proposes; arithmetic disposes.** [`src/recon/agent/guard.py`](src/recon/agent/guard.py)
enforces this. Generated prose is scanned for every rupee figure it contains,
and the whole output is rejected if even one was not computed by the engine — a
plausible wrong number in a finance note is worse than no note, because it is a
figure someone will quote to their bank. Rejections are counted and reported as
a **guard rejection rate**, because how often the model had to be overruled is a
fact about the system worth publishing.

### What the model actually turned out to be worth

Less than I expected, and the honest answer is in
[`docs/FAILURE_LOG.md`](docs/FAILURE_LOG.md).

I built `MISSING_UTR` — bank rows whose reference field is unusable — expecting
it to be the case that justified a narration-resolving model. Before writing the
engine I recorded a prediction: *deterministic matching will resolve ≥90% of
these on its own.* **It resolves 100%** (9/9), via exact amount-and-date
fallback.

I did not respond by making the data harder so the model would look necessary.
Manufacturing ambiguity to justify an architecture is precisely the failure the
"AI judgment" criterion is testing for. The narration resolver stays in, gated
behind arithmetic, invoked only on genuine residue — and the residue is
currently near zero. The model's real, unambiguous value is writing the
exception notes a human has to act on.

---

## Honest measurement

### The main set cannot produce a real accuracy number

Twelve defect classes, one per settlement, stratified so every class has ≥5
instances. The engine scores 100% on all of them. That number is worth almost
nothing, and I tried twice to break it:

- **Overlapped the magnitude bands.** Injected true mismatches now range from
  Rs 20 upward, overlapping plausible bank transfer charges, so no size
  threshold can separate them. Still 100% — because I replaced the threshold
  with an evidence rule (require an itemised charge row on the statement) that
  is genuinely correct.
- **Added a class only three-way reconciliation can catch.** Offsetting
  line-level errors that leave the settlement total tying to the paise. Caught
  perfectly, because I wrote the check at the same time as the defect.

The second failure was the useful one: it showed the problem is **structural**.
Any defect I invent is one I already know how to detect. No amount of tuning the
generator escapes that.

### So: a holdout of compound defects

[`src/recon/adversarial.py`](src/recon/adversarial.py) builds settlements
carrying **two defects at once**. The classifier is single-label with an ordered
rule chain, so compounds sit outside its design by construction.

It failed immediately, and correctly:

```
False-clear rate      33.3%   (4 of 12)   [FAIL]
False-escalate rate   33.3%
```

Both causes were real bugs in my engine:

1. **Phantom refunds were cleared.** The three-way check compared only *payment*
   lines against the order ledger. A refund reduces the payout and the bank
   credit identically, so it always ties — meaning a refund for an order the
   books never refunded was invisible to every totals-based check. I had written
   a three-way reconciler that only did three-way on the direction where money
   comes *in*. That is the shape of a misposted or fraudulent refund, and it
   went straight through.
2. **Consolidated payouts with a transfer charge were escalated as never paid.**
   Subset-sum requires exact totals, and a Rs 23 charge broke exactness.

Both fixed; both pinned with regression tests. Post-fix: 0 false clears, holding
across four independent seeds.

### The caveat that undercuts my own headline

Once I fixed the engine against that holdout, **it stopped being unseen data.**
It is training data now, and 0% on it is not an independent measurement. What I
can honestly claim is that the fixes generalise across seeds *within the same
compound families*. That is weaker than generalising to compound types nobody
has invented yet. A genuinely independent number needs defect families written
by someone who has not read `classify.py`.

---

## The exception list

Every escalation names the money, the evidence, and a next action:

```
setl_4TC0N0K4440284  ledger_mismatch                   Rs 0.00
    1 refund(s) totalling Rs 5,847.41 were debited from this payout for
    order(s) the ledger still records as not refunded (order_JECZYYKK6G0212).
    The settlement ties to the bank exactly, because the refund reduces both
    sides equally -- the books are the only source that disagrees.
    ACTION: Verify each refund against the order record before closing. An
    uncorroborated refund is either a misposting or money leaving on an
    instruction nobody authorised.
```

Note the `Rs 0.00` delta. Every total balances. This is exactly the case a
bank-versus-payout reconciliation is blind to.

`out/report.html` renders the same thing for a human: the false-clear rate
first and largest with a pass/fail verdict, then the exception list as cards,
each carrying its action. Settlements that balance perfectly but still escalate
get an explicit callout, because "delta Rs 0.00" next to "needs a human" looks
like a bug until you know it is the entire point. Single file, no CDN, no
webfont, light and dark, and it prints.

---

## Running it

```bash
python demo.py                      # everything, one command
open out/report.html                # visual report (self-contained, offline)
python -m pytest tests/ -q          # 72 tests
python eval/run_eval.py             # regenerate eval/metrics.md
```

Individual stages:

```bash
python -m recon.generate --out data --seed 42 --settlements 120
python -m recon.cli --input data --out out
python -m recon.adversarial --out data/holdout --seed 1337
```

The agent layer is **off by default** — the deterministic engine is the product,
and an engine that only reconciles when a network call succeeds is not one a
finance team can depend on. To enable it:

```bash
pip install -e ".[agent]"
export ANTHROPIC_API_KEY=...
python -m recon.cli --input data --out out --llm
```

> **Not measured against the live API.** No API key was available, so there are
> no real token counts, latency or cost figures. Rather than estimate them,
> `eval/metrics.md` records the layer as unmeasured. The command above prints
> the guard rejection rate and cost per 100 records the moment a key exists.

What *is* verified offline is the part I am responsible for: that the engine
behaves correctly when a model misbehaves.

```bash
python -m recon.cli --input data --llm-stub hallucinating
```

`--llm-stub` drives the real agent code path with a scripted client that
misbehaves on purpose — `honest`, `hallucinating`, `overreaching`, `failing`,
`refusing`. Waiting for a real model to eventually invent a figure is not a
test; scripting one that definitely does makes the safety property a
deterministic assertion.

**The invariant, asserted across all five scenarios:** whatever the model does —
lies, overreaches, dies, or refuses — the set of settlements escalated to a
human does not shrink, and **not one verdict moves**. The model may improve the
prose and nothing else.

```
guard rejections      49/49 (100.0%)
    amount_mismatch: 30
    invented_figure:9999999.99: 19
narration notes       0 accepted
```

A stubbed run prints a loud banner, reports its model as `SCRIPTED-STUB[...]`,
and suppresses the cost line — the stub's token figures are fabricated, and a
fabricated cost sitting where a real one goes is the exact thing this project
argues against.

Determinism: the same seed produces byte-identical output. `python -m
recon.generate --out d1 --seed 42` twice and diff the directories.

---

## Layout

```
src/recon/
  models.py            entities; integer-paise money; the defect taxonomy
  generate.py          synthetic three-source data + ground-truth answer key
  adversarial.py       compound-defect holdout (the only honest measurement)
  ingest.py            CSV -> typed records, converted to paise at the boundary
  engine/
    arithmetic.py      tolerances, each with its justification AND failure mode
    matcher.py         UTR join -> amount+date -> bounded exact subset-sum
    classify.py        ordered rules; default verdict is "I don't know"
    pipeline.py        the run, with the optional agent pass
  agent/
    guard.py           re-verification of everything a model returns
    fake.py            scripted misbehaving client, for testing the guard
    resolve.py         narration -> settlement identity (proposal only)
    narrate.py         exception notes for humans
    llm.py             client, model config, token and cost accounting
  report.py            scoring; false-clear rate first, always
  report_html.py       self-contained HTML report; no CDN, no webfont
eval/run_eval.py       regenerates eval/metrics.md
docs/FAILURE_LOG.md    what broke, kept live rather than reconstructed
```

## Known limitations

- **Sub-rupee blindness.** Differences ≤5 paise are absorbed as fee/GST rounding,
  so this cannot detect sub-rupee skimming. Deliberate — chasing 3 paise costs
  more than 3 paise — but it is a real hole and it is stated rather than hidden.
- **Single-label classification.** A settlement with two defects gets one reason
  code. Disposition stays correct; the explanation is partial.
- **Bank charges are recognised by narration keywords** (`CHARGE`, `COMMISSION`,
  `FEE`). A bank wording its fees differently gets its shortfalls escalated as
  unexplained — safe, but noisy. Production should match against the bank's
  published fee schedule, not a word list.
- **Anomaly rates are inflated** far above the 1–2% a real merchant sees, so the
  rare classes have enough support to be scored at all.
- **Synthetic data throughout.** No real settlement report was used, and real
  ones are messier in ways this generator does not imagine.

The schema mirrors Razorpay's actual
[settlement recon report](https://razorpay.com/docs/api/settlements/fetch-recon/)
fields (`entity_id`, `type`, `debit`, `credit`, `fee`, `tax`, `settlement_utr`, …).
