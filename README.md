# Three-way settlement reconciliation

[![CI](https://github.com/VanshS-21/Razorpay-Submission/actions/workflows/ci.yml/badge.svg)](https://github.com/VanshS-21/Razorpay-Submission/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

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
| False-escalate rate | 0.0% (0/107) | 0.0% (0/12) |
| Match rate | 84.9% | 50.0% |
| Reason-code accuracy | 100.0% | 100.0%, of which 83.3% exact primary |
| Unexplained bank rows | 30, all reported | 0 |
| Throughput | 1,149 lines — **tens of thousands of lines/sec**; see [`eval/metrics.md`](eval/metrics.md) | |

186 tests. No API key or network required for any of them.

Reason-code accuracy on the holdout counts either of a compound's two real
defects as correct, because with two defects present there is no single right
answer; the exact-primary figure sits beside it so nothing hides behind the
generous reading.

Throughput is deliberately not quoted as a single figure: nine identical runs
here spanned 24k-51k lines/sec, so three significant figures would be measuring
the machine's mood. `eval/metrics.md` reports the median and the range it came
from. At 1,149 lines it is not a scalability claim in any case - the dataset
fits in cache, and pass 3 is quadratic in settlement count.

The two rates have different denominators, deliberately: false clears over the
units that **must escalate**, false escalates over the units that **should
reconcile**. Each is the population its error can actually occur in. Dividing
either by every settlement would have been the flattering choice.

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
enforces this. Generated prose is scanned for every rupee figure it contains —
`Rs`, `Rs.`, `INR`, `₹`, the HTML entity, and amounts with the unit trailing,
case-insensitively — and the whole output is rejected if even one figure was not
computed by the engine for that settlement. A plausible wrong number in a
finance note is worse than no note, because it is a figure someone will quote to
their bank. Rejections are counted and reported as a **guard rejection rate**,
because how often the model had to be overruled is a fact about the system worth
publishing.

**What that check is not.** It verifies that every figure is *real*, not that
the sentence around it is *true*. The allowed set for one settlement is every
value the engine derived for it — a median of 35, up to 60 — so a note that cites
a real figure in the wrong role, or gets a direction backwards ("credited more"
where the bank credited less), passes. Catching that needs semantic
verification, not a pattern, and this project does not claim to do it. The
original pattern also matched only the single spelling `Rs 1,234.56`, and its
tests used only that spelling, so they confirmed the implementation rather than
the property; nine other ordinary spellings walked straight past it until an
audit enumerated them.

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

- **Overlapped the magnitude bands.** The generator draws true mismatches from
  Rs 20 upward, into the range of a plausible bank transfer charge, so no size
  threshold can separate the two classes. Still 100% — because the threshold was
  replaced with an evidence rule (require an itemised charge row on the
  statement) that is genuinely correct.

  *This paragraph used to overstate itself, and an external audit caught it.* On
  seed 42 the bands happen not to overlap: the smallest injected mismatch is
  Rs 224 and the largest charge Rs 59. Meanwhile a shipped test asserted that
  mismatches are always ≥ Rs 100, and a comment in `arithmetic.py` said the
  bands do not overlap and that the README said so — while the README said the
  opposite. Three artifacts, three stories. That test has been replaced by
  `test_engine_holds_where_the_magnitude_bands_overlap`, which runs five seeds
  where the smallest mismatch really is *smaller* than the largest charge, and
  asserts every one of them still escalates.
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

`out/report.html` renders the same thing for a human. It is set as a control
document rather than a dashboard: Swiss neo-grotesque on a near-white sheet, an
exposed 12-column hairline grid, and **exactly one signal ink — red, spent
entirely on risk**. Reconciled state carries no colour at all, so on this page
"fine" is the absence of red, which is how an audit document should read.

The false-clear rate leads, before the flattering numbers, as a label-left
figure-right row like every other — the point is that it is read first, not that
it shouts. Red is spent on one thing only: money at risk. Settlements that
balance perfectly but still escalate get an explicit callout, because "Rs 0.00"
beside "needs a human" reads as a bug until you know it is the entire point of
holding a third source.

Single file, ~130 KB. IBM Plex Sans and IBM Plex Mono (SIL Open Font License
1.1, see [`OFL.txt`](OFL.txt)) are embedded as data URIs, so there is no CDN, no
webfont request, and no external asset — it opens offline from a fresh clone and
prints clean. Tokens are exported to [`docs/tokens.css`](docs/tokens.css).

---

## Running it

```bash
python demo.py                      # everything, one command
open out/report.html                # visual report (self-contained, offline)
pip install -e ".[dev]"             # pytest
python -m pytest tests/ -q          # 186 tests
python eval/run_eval.py             # regenerate eval/metrics.md
```

Individual stages:

```bash
pip install -e .                    # required: these run as modules
python -m recon.generate --out data --seed 42 --settlements 120
python -m recon.cli --input data --out out
python -m recon.adversarial --out data/holdout --seed 1337
```

`demo.py`, `pytest` and `eval/run_eval.py` each put `src/` on the path
themselves and need no install. The four commands above do not, and used to fail
with `ModuleNotFoundError` on a fresh clone.

The agent layer is **off by default** — the deterministic engine is the product,
and an engine that only reconciles when a network call succeeds is not one a
finance team can depend on. To enable it:

```bash
pip install -e ".[agent]"     # Anthropic
pip install -e ".[gemini]"    # or Google
python -m recon.cli --input data --out out --llm --provider gemini
```

### The model is a replaceable part, and that is checkable

Two vendors sit behind one adapter. Everything above it — the prompts,
`guard.py`, `narrate.py`, `resolve.py` — is written against a single method:
*here is a system instruction, a prompt and a schema; give me parsed JSON and
tell me what it cost.* Which company answers lives in `agent/llm.py` and nowhere
else.

`--provider auto` picks whichever API key is in the environment, preferring
Anthropic if both are set. Run the batch twice against different backends and
compare the verdicts:

```bash
jq '.findings[]|{settlement_id,disposition,reason_code,delta}' out/run.json
```

**Not one disposition, reason code or delta moves.** That is structurally
guaranteed rather than merely observed: `_attach_proposals` writes only
`action_required`, `narrate_exceptions` writes only `explanation`,
`action_required` and `resolved_by`, and no code path re-enters the classifier —
so there is nothing a model could say that could move a verdict. It has been
checked against two backends with different wire shapes; never against two live
vendors, because only one key was available.

Diff the whole file and you will see ~38 fields change across 126 findings, plus
the timing block. Those are the prose fields, and they are *supposed* to move.
The four above are the claim.

### What a real run measured

Measured against **Gemini**, not Claude. No Anthropic key was available, so the
Anthropic backend has never met a live API — it is exercised only by the scripted
stub, which is a wire shape I wrote myself, and that circularity is stated here
rather than hidden.

The whole live record is two calls, against `gemini-3.5-flash`, committed at
`out/agent.json` so the arithmetic below is checkable like everything else here:

```
885 input · 337 output · 2,308 thinking tokens · $0.025133
```

Two things follow, and one does not:

- **Thinking tokens are 87% of the output and 83% of the bill, and appear
  nowhere in the reply.** Gemini reasons before answering; that reasoning is
  billed at the output rate and is *not* included in `total_output_tokens`.
  Counting only output tokens understated this run by **5.8×**.
  `Usage.thought_tokens` counts them separately and bills them as output.
- **A full batch needs 19 model calls; the free tier allows 20 per day** on
  `gemini-3.7-flash`, which is the default and which I have not run. It only
  fits because 30 wasted calls were removed (see `docs/FAILURE_LOG.md`). The same
  batch reconciles in 0.04 seconds with **zero** model calls and identical
  verdicts. The model is a garnish; the books close without it.
- **What a full batch costs is not known.** `--narrate-limit 2` paid for two
  notes and the report divided that across all 126 settlements, reporting $0.0199
  per 100 records for work it had not done. `per_n_records` now refuses to scale
  a capped batch. Two calls is a sample, not a measurement, and this section
  deliberately quotes no per-batch figure — an earlier draft named one, and that
  figure was the same forbidden extrapolation done by hand.

Cost is reported only for a model whose price has actually been read, and only
when the whole batch ran. An unpriced model prints no figure at all, because
`$0.0000` reads as "free" rather than as "unknown".

What *is* verified offline is the part I am responsible for: that the engine
behaves correctly when a model misbehaves.

```bash
python -m recon.cli --input data --llm-stub hallucinating
```

`--llm-stub` drives the real agent code path with a scripted client that
misbehaves on purpose — `honest`, `hallucinating`, `overreaching`, `failing`,
`refusing`, `truncated`, `plausible`. Waiting for a real model to eventually
invent a figure is not a test; scripting one that definitely does makes the
safety property a deterministic assertion.

Two of these produce output identical to `honest` on the shipped dataset:
`overreaching` and `plausible` both act on the *resolver*, and the shipped data
leaves no ambiguous bank rows for the resolver to be asked about — it makes zero
calls. They are exercised by tests that build their own ambiguous fixtures, not
by the demo. `hallucinating` is the one to run if you want to watch the guard
work.

**The invariant:** whatever the model does — lies, overreaches, dies, refuses,
or answers entirely plausibly — the set of settlements escalated to a human does
not shrink, and **not one verdict moves**. The model may improve the prose and
nothing else.

That claim used to be weaker than it sounded, and an external audit is why it is
not any more. The model *was* allowed to place a bank row when its proposal
passed the guard, and the guard re-applied the same exact-amount-and-date test
the matcher had already run. On an ambiguous row that test cannot discriminate —
a row is ambiguous precisely *because* several unpaid settlements tie on amount
inside the window — so every candidate passed it, and whichever one the model
named got cleared. A verdict moved from exception to reconciled on a coin flip,
and because `classify()` rebuilt the finding from scratch afterwards, the output
attributed the result to the deterministic engine.

The five-scenario test did not catch it: the ambiguous set is empty on both
shipped datasets and every orphan proposal failed on amount, so the accept path
never executed in any scenario. An invariant guarded only by a path that never
runs is a coincidence.

The model now returns **leads, never placements**. A proposal is attached to the
exception it concerns as a line the human can act on, and is never handed to the
classifier — so a verdict cannot move because no code exists that could move
one, rather than because a test asserted it on data where the question never
arose. A sixth scenario, `plausible`, exercises the case directly, and
`test_an_ambiguous_row_is_never_resolved_by_the_model` builds the
two-identical-nets input the shipped data never produces.

```
provider              none (scripted stub, wire shape: anthropic)
model                 SCRIPTED-STUB[hallucinating]
narration notes       0 accepted
guard rejections      19/19 (100.0%)
    invented_figure:9999999.99: 19
```

The `amount_mismatch` rejections that used to appear here are gone, and their
absence is a fix rather than a regression: 30 of those 49 calls were asking the
model to identify settlements that subset-sum had **already reconciled** — 61% of
every run spent on questions with no possible answer. See `docs/FAILURE_LOG.md`.

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
                       (decimal rupees or integer paise; a cell that is neither
                       is refused by file, row and column, never read as zero)
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
- **A consolidated transfer is cleared on arithmetic alone.** Nothing in the
  statement names the other payouts in the group: the bank quotes one UTR and
  the rest is inferred from the fact that their nets add up. That inference is
  the reason a subset-sum solver has to exist, and it is almost always right —
  but *"these payouts sum to this credit"* and *"this credit paid these
  payouts"* are different statements. An over-credit on one settlement sitting
  beside an unrelated unpaid payout of exactly the right size satisfies the
  first and not the second, and the engine will clear both. It is
  indistinguishable from a genuine consolidation *in the data*, so no rule fixes
  it. What changed after the audit found it: the solver now refuses any target
  with more than one valid subset, checks the window against every credit row
  rather than one anchor, and every such clear is marked `deterministic:inferred`
  at 0.90 confidence and listed under **SPOT CHECK** in the report. Cleared, but
  never quietly.
- **The guard checks figures, not claims.** See above: a real number used in the
  wrong role survives it.
- **A zero false-clear rate is scored against the answer key, not against
  reality.** The key is written by the same generator that writes the data, so
  it can only mark a settlement wrong in a way the generator knows how to
  produce. A defect outside the generator's vocabulary makes the engine wrong
  and the rate still read 0.0%. Three such shapes were found by the third audit
  and are now escalated (an uncorroborated refund, an uncorroborated payment, an
  adjustment line) -- but the general point survives the fix: the number answers
  a narrower question than "nothing was missed", and it is quoted here as the
  answer to that narrower question.
- **Charge attribution needs an unambiguous day.** An unreferenced bank charge
  is attached to a settlement only when its value date carries exactly one. Both
  generators emit one payout a day, so the rule always fires in testing; a real
  merchant has dozens, and it never will. The charge then stays in the orphan
  list and is reported rather than attributed, which is the safe direction --
  but the holdout's `missing_utr + bank_charge` family measures less than it
  looks like it does.
- **Contested consolidations clear nothing.** When two over-credited settlements
  could each be explained by the same unpaid payout, neither is cleared and both
  escalate. Correct -- one payout pays for one thing -- but it means a busy
  statement will escalate more than a quiet one for reasons that are about
  ambiguity rather than about error.
- **One currency.** `currency` is read from all three sources and anything other
  than INR is refused at ingest, loudly. Every total here assumes a single unit,
  and reconciling across two without a rate is adding numbers that are not the
  same kind of thing. Multi-currency merchants are simply out of scope.
- **Money units are inferred per file.** A decimal point anywhere in a money
  column means the file is in rupees; a file with none is read as paise. Real
  exports are internally consistent about this, but a file that mixes both
  conventions genuinely is ambiguous and will be read wrong -- in the loud
  direction, not the quiet one.
- **Cost accounting uses floats.** The reconciliation path is integer paise
  throughout, with no exceptions; the USD/INR estimate in `agent/llm.py` is not.
  It never touches a settlement.
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
