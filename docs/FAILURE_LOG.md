# Failure log

Kept live, in order, as things broke. Not reconstructed afterwards.

---

## Day 1

### The answer key was silently wrong twice

The generator emits the data *and* the ground-truth key. That makes the key the
single point of failure for the whole project: if it is wrong, the engine gets
graded against the wrong answers and every accuracy number in the README is
fiction — with no symptom, because the tests would still be green.

Two defects, both found by writing invariant tests before writing the engine:

**1. `FEE_TAX_ROUNDING` was indistinguishable from `CLEAN`.** I perturbed the
per-line `fee`/`tax` by a few paise to simulate rounding drift, then computed
the bank credit as the sum of the perturbed lines. So the delta came out as
exactly zero. The class that exists to test the tolerance band was generating
units that tied perfectly. Fix: the bank credit is now computed on the
aggregate and drifts from the per-line sum by ±1–3 paise, which is what
actually happens when a PSP rounds per line and a bank moves one lump sum.

**2. Split-refund remainders leaked into `CLEAN` settlements.** Second halves of
split refunds were drained into whichever settlement came next. When that was a
`CLEAN` one, the unit ended up containing a partial refund while its key said
"nothing unusual here". Fix: remainders now drain only into settlements already
labelled `SPLIT_REFUND`, and `test_clean_settlements_are_actually_clean`
asserts a `CLEAN` unit contains payment lines only.

The lesson I actually took: the test that mattered was
`test_delta_equals_injected_delta` — asserting that observed minus expected
equals exactly what was injected, for all 120 settlements. It is the invariant
that makes the key falsifiable, and both bugs died the moment it existed.

### The LLM may not be needed for the class I built for it

`MISSING_UTR` exists so a language model can recover a settlement's identity
from bank narration prose when the reference field is unusable. Having built
the data, the honest observation is that **a deterministic amount-plus-date
match probably solves most of it** — settlement nets are large and varied, so
exact-amount collisions should be rare.

Deliberately not fixing this by making the data harder. Contriving ambiguity so
the LLM looks necessary would be the exact failure the "AI judgment" criterion
is testing for. The plan instead: build the deterministic matcher first,
measure how much residue is actually left, and let the model handle only that.
If the residue turns out to be small, the README will say so with a number.

Prediction recorded before writing the engine, so it can be scored honestly:
**deterministic matching resolves ≥90% of `MISSING_UTR` units on its own.**

---

## Day 2

### The main dataset cannot produce an honest accuracy number

The deterministic engine scored 100% on every class, with a 0% false-clear rate.
That is not a result. I wrote the generator and I wrote the classifier, so every
defect I inject is one I already know how to detect — the two are the same
assumptions written twice. Perfect scores measure internal consistency and
nothing else.

I tried twice to fix this by making the data harder, and both attempts failed to
move the number:

- Overlapped the bank-charge and true-mismatch magnitude ranges so no size
  threshold could separate them. The engine still scored 100%, because I
  replaced the threshold with an evidence rule (look for an itemised charge row)
  that is genuinely correct.
- Added a class only three-way reconciliation can catch — offsetting line-level
  errors that leave the settlement total tying to the paise. Caught perfectly,
  because I wrote the check at the same time as the defect.

That second failure was the useful one: it proved the problem is structural, not
a matter of difficulty. No amount of tuning the generator escapes it.

### The fix: a holdout of compound defects

`src/recon/adversarial.py` builds settlements carrying TWO defects at once. The
classifier is single-label with an ordered rule chain, so compounds sit outside
its design by construction. Nothing in that file was reverse engineered from the
classifier.

It found real bugs immediately:

```
False-clear rate      33.3%   (4 of 12 must-escalate units)  [FAIL]
False-escalate rate   33.3%   (8 units)
Match rate            33.3%
```

**The false clears are the serious one.** All four are phantom refunds: the PSP
report debits a refund for an order the merchant's books still record as `paid`
and never refunded. The settlement ties to the bank to the paise, so every
total-based check passes, and my classifier reaches `REFUND_NETTED_LATER` and
clears it. That is the exact shape of a misposted or fraudulent refund, and my
engine waves it through.

The cause is narrow and my own: `ledger_mismatches()` only ever compared
**payment** lines to the order ledger. Refund lines — the ones that take money
out — were never checked against the books at all. I wrote a three-way
reconciler that only did three-way on the direction where money comes in.

The false escalates are less dangerous but real: a transfer charge levied on a
consolidated payout breaks the exact-sum requirement, so the subset-sum solver
finds nothing and both settlements report as never paid.

### Both fixed

**Phantom refunds** — `classify.unsupported_refunds()` now checks every refund
line against the order ledger's status, not just payment lines against its
amounts. A refund for an order the books record as `paid` escalates as
`LEDGER_MISMATCH` with the order ids named.

**Consolidated payout plus charge** — the subset-sum solver now excludes
itemised charge rows before computing the surplus. A charge is a fee *on* the
transfer, not part of the payout it carries, so it has no business inside the
payout arithmetic.

```
                    before      after
false-clear rate    33.3%       0.0%
false-escalate      33.3%       0.0%
match rate          33.3%      50.0%
```

Main set unchanged: no regression, still 0 false clears.

**The caveat I have to state, because it undercuts the numbers above.** Once I
fixed the engine against this holdout, the holdout stopped being unseen data. It
is training data now, and 0% on it is not an independent measurement. What I can
honestly claim is that the fixes hold across four different seeds of the same
compound families — a weaker claim than generalising to compound types I have
not thought of yet. Both numbers are in `eval/metrics.md` with this caveat
attached, rather than the flattering one on its own.

### Prediction from Day 1, scored

I predicted deterministic matching would resolve ≥90% of `MISSING_UTR` on its
own, making the language model largely unnecessary for that class. **It resolves
100% of them** (9/9 on the main set) via exact amount-and-date fallback. The
prediction was right and slightly understated.

So the narration resolver has almost no matching work to do. That is a real
finding about where the model does and does not belong, and the honest response
is to keep the model out of the matching path rather than route work through it
to justify the architecture.

---

## Day 3

### The agent layer is built but unmeasured, and I am not going to estimate it

The narration resolver and the exception-note writer are implemented, guarded,
and unit-tested offline (`tests/test_guard.py`, 13 tests, no API key needed —
the safety logic is pure functions precisely so it can be tested that way).

But there is no `ANTHROPIC_API_KEY` and no `ant` CLI in the environment I built
this in, so I have never run a real call. I have no token counts, no latency, no
cost per 100 records.

The tempting move is to estimate: token-count the prompts, multiply by the
published rate, and put a plausible number in the README. I am not doing that,
because a estimated cost figure presented next to measured accuracy figures
reads as measured, and the entire point of this project is that the numbers in
it are checkable. `eval/metrics.md` says "not measured" and gives the exact
command that would measure it.

This is also why the agent layer is off by default and why `--llm` **fails
loudly** with exit code 3 rather than silently degrading. A run that was meant
to use the model must never quietly pretend it did.

### The guard is the part I would defend in an interview

`verify_narration()` extracts every rupee figure from model-generated prose and
rejects the entire output if even one was not computed by the engine. Not
"flags for review" — rejects, and falls back to the deterministic text.

The reasoning: a plausible wrong number in a finance note is worse than no note
at all. Nobody acts on a missing explanation. People act on a number, and the
action here is quoting it to a bank. So the failure mode of a slightly-wrong
narration is a finance analyst confidently telling their bank the wrong figure.

`verify_match()` is the same idea for identity: the model may propose which
payout a bank row belongs to, and the proposal is then re-run through the exact
amount and date-window test the deterministic matcher uses. An accepted proposal
is one the engine could defend without ever mentioning that a model was
involved. `test_match_rejected_when_amount_is_off_by_one_paisa` pins that —
exact means exact.

---

## Day 4

### No card, no API key, no measurement — so I tested the part that is mine

Anthropic's Console needs a card and does not take UPI, so I cannot buy API
credits and cannot make a single real call. The agent layer stays unmeasured.

What I can do is separate two questions that were tangled together:

- *How good is the model?* — unanswerable here. Left unanswered, and marked as
  unanswered in `eval/metrics.md`.
- *Does my code behave correctly when the model misbehaves?* — entirely
  answerable offline, and more my responsibility anyway.

`src/recon/agent/fake.py` is a scripted client that misbehaves on purpose:
`honest`, `hallucinating`, `overreaching`, `failing`, `refusing`. Waiting for a
real model to eventually invent a number is not a test. Scripting one that
definitely does makes the safety property a deterministic assertion.

The headline invariant, asserted across all five scenarios: **whatever the model
does — lies, overreaches, dies, or refuses — the set of settlements escalated to
a human does not shrink, and not one verdict moves.** The model may improve the
prose and nothing else.

> **This claim was wrong when I wrote it.** See Day 5 — the test could not fail,
> and the invariant it was guarding did not hold. I have left the paragraph as
> written rather than editing it, because the point of this log is what I
> actually believed at the time.

To keep this honest, a stubbed run prints a loud banner, reports its model id as
`SCRIPTED-STUB[...]`, and suppresses the cost line entirely, because the token
figures the stub produces are fabricated and a fabricated cost sitting where a
real one goes is exactly the thing this project is arguing against.

### The harness immediately found a bug in my guard

Running the `honest` scenario, the guard rejected 10 of 19 perfectly valid
notes. Reason code: `invented_figure:0.00`.

`allowed_figures_for()` ended with `vals.discard(0)`. I had written that
thinking "Rs 0.00 is never a figure worth citing." That is exactly backwards.
In a ledger mismatch or a phantom refund **the difference IS zero** — the
settlement ties to the paise and only the books disagree — and saying so is the
single most important sentence in the note. My own deterministic text says "the
errors net to Rs 0.00 across the settlement."

So the guard was rejecting the truest sentence in the report, and it would have
done so silently in production: the note falls back to the deterministic text,
nothing errors, and the only symptom is a rejection-rate metric nobody was
watching yet.

One line deleted, one regression test added (`test_zero_is_a_citable_figure`).

The general lesson, which I did not expect going in: a guard needs its **accept**
path tested as hard as its reject path. A guard that rejects everything is
trivially safe and completely useless, and mine was quietly drifting that way.

---

## Day 5 — a review pass (AI-assisted)

I wrote a deliberately adversarial prompt and gave it to an agent with no
context on the project and no stake in it: verify every claim against the code,
treat a gap between documentation and behaviour as the most severe finding
available, and do not be agreeable. Then I verified its findings myself before
acting on any of them, because an agent's confident output is exactly the thing
this project is built not to trust.

It found five things that mattered. Four of them were the same mistake.

### The claim I was proudest of was the one that was false

*"Whatever the model does, not one verdict moves."* The audit moved two.

The model was allowed to place a bank row when its proposal passed
`verify_match`, and `verify_match` re-applied the same exact-amount-and-date
test the matcher had already run. That sounds airtight and is circular: rows
reach the model **because** arithmetic could not identify them, and a row is
ambiguous precisely because several unpaid settlements tie on amount inside the
window. Every candidate passes a test that is true of all of them by
construction. Whichever one the model named got cleared — a verdict moving from
exception to reconciled on a coin flip. Then `classify()` rebuilt the finding
from scratch, so `resolved_by` said `deterministic` and confidence said 1.00.

Why five scenarios and a parametrised test never caught it: `m.ambiguous` is
empty on both shipped datasets, and every orphan proposal fails on amount. So
`matches_accepted == 0` in all five scenarios and the accept path never
executed. **The test passed because the code it guarded never ran.**

That is the lesson I want to keep from this whole build. A test over a path
that cannot execute is not weak evidence, it is *no* evidence, and it is worse
than no test because it reads like proof. I had already learned the neighbouring
lesson on Day 4 — that a guard needs its accept path tested as hard as its
reject path — and still did not check whether the accept path was reachable.

The fix is not a better guard. The model no longer places anything: it returns
leads, attached to the exception for the human, never handed to the classifier.
The guarantee is now structural rather than asserted. A sixth stub scenario,
`plausible`, plus a two-identical-nets fixture, exercise the path that used to
be unreachable.

### Three artifacts, three different stories

The README said I had overlapped the true-mismatch and bank-charge magnitude
bands so no size threshold could separate them, and counted it as a hardening
step. A comment in `arithmetic.py` said the two bands **do not** overlap and
that "the README says so explicitly." A shipped test asserted mismatches are
always ≥ Rs 100 — enforcing the separation the README claimed to have removed.
The shipped data had a 3.8× gap.

Every one of those was written by me, within a few hours of the others. The
generator really can draw from Rs 20; seed 42 just did not. The rule really is
evidence-based and correct — I confirmed it holds on seeds where the bands
genuinely overlap. But a submission whose entire argument is "my numbers are
honest and checkable" cannot afford a paragraph describing work that is not in
the repository. That is the finding I would have been most embarrassed to have
a judge make.

### The generator's key was wrong again, in the same class as Day 1

Three defect classes act on an order from an *earlier* settlement. When one
landed first in the shuffled schedule there was no earlier order, so the
injection was skipped with `if prior_orders:` — and the ground-truth record was
written anyway. The key claimed a chargeback that was not in the data. Five
occurrences across forty seeds.

`test_delta_equals_injected_delta` cannot see this: a chargeback that was never
injected has a delta of zero, and zero is exactly what the key records. Day 1
established that the key is this project's single point of failure and that
invariant tests are how you defend it. I then wrote no test asserting that a
settlement labelled X actually contains X. That test exists now
(`test_a_labelled_defect_is_actually_present`), and the schedule guarantees a
history-independent class goes first.

### Seven mutants, seven surviving

The audit mutated constants and rules and re-ran the suite. Widening the
rounding tolerance a hundredfold: 72 passed. Emptying the bank-charge
vocabulary: 72 passed. Turning *"no bank credit found for this payout"* into
`RECONCILED` — auto-clearing money the merchant never received: 72 passed.

The dispositions I had pinned were pinned well. The thresholds underneath them
were not pinned at all, and one of them is the constant behind a documented
limitation, which makes its value a published claim. There was also no fixture
anywhere in the suite where a payout simply never arrived, because the generator
always credits something.

All seven are killed now, each by a test that says why it exists.

### What the audit could not fix

An over-credit on one settlement sitting beside an unrelated unpaid payout of
exactly the right size is arithmetically identical to a genuine consolidated
transfer. The engine clears both. I could not find a rule that separates them,
because in the data there is no difference — only the bank knows. So it stays
cleared, but it is now marked `deterministic:inferred`, carries 0.90 confidence,
and is listed under **SPOT CHECK** in every report, with the limitation stated
in the README. Making a weakness visible is not the same as fixing it, and I
would rather say so than quietly widen a tolerance until the symptom goes away.

### Reproducing the Day 3 numbers

The holdout and both fixes landed in the same commit, so there is no commit you
can check out where the engine fails at 33.3%. To reconstruct it: revert
`unsupported_refunds()` in `engine/classify.py` and the charge-row exclusion
from the surplus computation in `engine/matcher.py`, then run the holdout. That
is a fair criticism of how I committed, not of the numbers — they reproduce
exactly.

---

## Day 6 — adding a second vendor, and what a free tier exposed

I had no Anthropic API key, so the agent layer had never run against a real
model. Every number about it came from a scripted stub I wrote myself, which is
the same circularity the main dataset suffers from: I wrote the misbehaviour and
I wrote the detector. A Gemini key I already had turned that into something
measurable, so I put both vendors behind one interface and pointed the engine at
Google instead.

Four things broke. Three of them are mine and only one is interesting for its
code; the rest are interesting for how they hid.

### Thinking tokens are 87% of the output and appear nowhere in the reply

The very first call returned 61 input tokens, 60 output tokens -- and 275
*thought* tokens. Gemini reasons before answering, that reasoning is billed at
the output rate, and it is not included in `total_output_tokens`.

Written the obvious way, counting `output_tokens` and nothing else, the reported
cost would have been **5.8x too low**. Nothing about a wrong figure there would
have looked wrong: it is a real number, correctly computed, describing the wrong
thing.

> Corrected on 2 Sept, after a third audit. The paragraph above originally read
> "across a real 19-call run, thinking was 87% of everything I paid for". Two
> errors in one clause. There was no 19-call run -- the live record is two calls,
> and I had written the number I expected a full batch to look like. And 87% is
> thinking's share of the *output tokens*, not of the bill; of the bill it is
> 83%, because input tokens are charged too. Getting the unit wrong on the one
> finding I was proudest of is the same mistake as the taxi meter below, made
> while writing up the taxi meter.

### 61% of the model calls were spent on questions with no answer

A full run made 49 calls. Nineteen wrote exception notes; the other thirty asked
the model to identify orphan bank rows on behalf of settlements that had *no
bank row assigned*.

Six of those settlements were resolved by subset-sum. They are part of a
consolidated group -- the credit sits against the anchor -- so `assigned[sid]` is
empty even though they are perfectly matched. `resolve_unmatched` checked only
`assigned` and never `group`, so it treated all six as unidentified and offered
the model every orphan row on the statement.

Thirty of forty-nine calls, none of which could ever have succeeded.

On a paid key this is a slightly larger invoice every month and nobody ever
looks. It was only visible because a free-tier quota of 20 requests per day made
running the thing impossible, which forced me to ask where the calls were going.
**The constraint was the diagnostic.** Cutting 49 to 19 also changed the layer
from unmeasurable to measurable: the free daily allowance is 20.

### Fixing the waste made a test vacuous, and I nearly relaxed it

`test_overreaching_match_proposals_are_all_rejected` asserted that the guard
rejects unsupported proposals with `amount_mismatch`. With the wasted calls gone
there were no proposals left on that dataset, so the test failed -- and the
tempting fix was to soften the assertion.

That is precisely the failure the audit caught me in five days ago: a test whose
guarded path never executes, passing because nothing can happen. Softening it
would have preserved the green tick and destroyed the evidence. Instead the test
now builds the input it actually needs -- two unmatched settlements of different
sizes and a credit matching neither -- so the model genuinely proposes something
and arithmetic genuinely refuses it.

Twice now I have written a test that could not fail. The habit I am trying to
build: after making a test pass, ask whether it *could* have failed.

### A capped run reported a cost 9.5x too low

The free quota meant I could only afford two notes, so I ran with
`--narrate-limit 2`. The report said **$0.0199 per 100 records**.

The two calls really did cost what they cost. But `per_n_records` divided that
across all 126 settlements, as though the whole batch had been narrated. Two
notes of nineteen.

A taxi meter reading Rs 50 after 2 km, written down as the price of a 20 km
journey. Wrong in the flattering direction, silently, in the one number an
operator would actually plan around. `per_n_records` now refuses to extrapolate a
capped run at all, and reports measured cost *per note* instead.

> Corrected on 2 Sept, after a third audit. This entry originally continued:
> "The honest figure is **$0.1895 per 100 records** -- 9.5x higher." That
> sentence is the bug it is reporting. $0.1895 is $0.0199 multiplied by 19/2 --
> the same extrapolation, done by hand, in the paragraph explaining why the code
> must never do it. "9.5x" was knowable before the run and measured by nothing.
> I wrote a fix that refuses to print a number, and then printed the number.
>
> The honest statement is that the run was capped at two notes, that those two
> notes cost $0.025133, and that **what a full batch costs is not known**. It
> survived two audits because it looks like a measurement and sits beside three
> real ones.

### What the guard rails did right

Two audit fixes earned their place on first contact with reality. When all 49
calls failed, the run exited 3 with "every one of the 49 model calls failed"
rather than printing `$0.0000` and exiting 0 -- which is exactly what it would
have done a week ago. And `python -m recon.cli` failed with `ModuleNotFoundError`
until `pip install -e .`, precisely as finding M3 said it would.

### The measurement, stated honestly

Two real narrations, both accepted, **zero guard rejections** -- against a real
model rather than a stub, for the first time. Two calls is a sample, not a
measurement, and it is labelled as one.

The operational finding matters more than the cost: a 126-settlement batch needs
19 model calls, and the free tier allows 20 per day. The same batch reconciles in
0.04 seconds with **zero** model calls and identical verdicts.

That is the architecture argument made by measurement rather than assertion. The
model writes prose. The arithmetic decides the money. Swap the vendor, or remove
it entirely, and not one verdict moves.

---

## Day 7

A third review pass, again AI-assisted, run with a brief that told the agent the easy findings were
gone and that its value was in the fixes themselves. It was right to be told
that: eleven of the fifteen mutants it found surviving were fixes applied in the
previous two days, each written under deadline, reviewed once, and shipped
without a test that would notice it being undone.

### Three false clears that the false-clear rate cannot see

The engine reported `0.0% [PASS]` on all three of these while money left the
account and nothing accounted for it:

- a **refund with the `order_id` column left empty**. `unsupported_refunds()`
  skipped any refund without one -- so the single defect this project advertises
  most, the one the adversarial holdout was built to catch, was bypassed by
  leaving a column blank;
- a **payment line naming an order the ledger has never heard of**. A fabricated
  refund was caught; a fabricated *sale* was invisible;
- an **adjustment line of any size**. `ADJUSTMENT` was in the taxonomy, the
  generator never emitted one, and no rule anywhere inspected one. Its debit
  flowed straight into `expected_net`, so the bank tied to the payout and the
  settlement cleared CLEAN.

All three share a shape the two documented false clears do not. The scored rate
was not lying. It was answering a narrower question than the README implied:
*the answer key cannot mark a settlement wrong in a way the generator does not
know how to produce.* A defect outside the generator's vocabulary makes the
engine wrong and leaves the number at 0.0%. That sentence is now in Known
limitations, and it is the more interesting half of the finding.

The fix is one rule rather than three, and it is the rule the whole engine
already rested on, applied to every line type instead of the two the generator
happens to emit: **a line that moves money must name an order the books know
about.** A blank id is less corroborated than a wrong one, not more.

### A whole-rupee cell was read as paise

`_int` decided units per **cell**: a decimal point meant rupees, its absence
meant paise. In a real export where `fee` is `16.95` on one row and `17` on the
next, the first was Rs 16.95 and the second Rs 0.17. No error, no warning, same
column, one row apart.

Worse than the blank cell this function had already been hardened against, and
in exactly the way that matters. A wrong zero at least fails to balance. This
scaled every column of a row by the same factor, so the settlement stayed
internally consistent, tied to the bank, and reconciled CLEAN at 1% of its true
value. Deciding units once per file fixes it and, as a side effect, lets the
engine read a rupee-denominated export at all -- which it previously could not.

### The reject path was widened without re-measuring the accept path

Day 4 recorded the danger in so many words: *a guard that rejects everything is
trivially safe and completely useless, and mine was quietly drifting that way.*
Then I broadened `_SYMBOL` to catch `INR`, compiled it with `re.I`, and left off
the leading word boundary. The `rs` at the end of an ordinary English plural
became a currency symbol. `orders 4471` extracted Rs 4,471.00. So did
`hours 48`, `customers 1200`, `numbers 88421`. Nine of fourteen common plurals
fired, and since an unrecognised figure rejects the whole note, the guard had
started rejecting **correct** notes -- worst on `orders`, the word a
`LEDGER_MISMATCH` note is necessarily about.

`test_an_order_id_is_not_read_as_money` tests precisely this concern and missed
it, because its fixture is `order_78910 was booked twice` -- no space between the
word and the digit. The right instinct, and a string that happened not to fire.

One character fixes it. What it cost was a day of the guard being wrong in the
direction I had already written down as the one to watch for.

### The number I was proudest of was the wrong unit, and the number beside it was not a measurement

Two errors in the same four bullets, and they point in opposite directions:

- "thinking tokens are 87% **of the bill**" -- 87.3% is thinking's share of the
  *output tokens*. Of the bill it is 82.6%, because input tokens are charged
  too. The two published figures were mutually inconsistent: 87% of the bill
  would imply a 7.7x understatement, not the 5.8x reported three lines above it.
- "the real figure was **$0.1895** per 100 records" -- $0.1895 is $0.0199 times
  19/2. It is the extrapolation, done by hand, inside the paragraph explaining
  why `per_n_records` must never do it. "9.5x" was knowable before the run and
  measured by nothing.

Both survived two prior audits because they look like measurements and sit
beside three real ones. The corrections are inline above, in the entries where
the claims were made, rather than quietly edited out.

`out/agent.json` is now force-added to the repository. Every deterministic
number here is checkable because the data and the key are committed; the model
numbers were the only ones that were not, and the file that fixes that was one
line of `.gitignore` away the whole time.

### Nineteen calls that were never made, reported as nineteen failures

The circuit breaker added on Day 6 stops after two calls exhaust their retries.
Correct. But the give-up went through `_debug()`, which prints nothing unless
`RECON_LLM_DEBUG` is set, and the skipped calls were counted as `errors` -- so
the console showed `api errors 19`, indistinguishable from nineteen refusals,
and the exit message said *"not one of the 19 model calls produced usable
output"* when two calls had been made and seventeen were never sent. A sentence
that states a false fact about the run, in the layer whose entire job is to
report honestly what the model did.

### The third instance of a lesson recorded twice

This log says, twice, that *a test over a path that cannot execute is not weak
evidence, it is no evidence*. The audit found the third instance live in the
code: deleting the `stop_reason in ("refusal", "max_tokens")` guard left all 135
tests green, because the `refusing` stub returned empty text and
`json.loads(text) if text else None` already returned `None` one line earlier.
`test_a_refusing_model_degrades_cleanly` passed through a path it did not test.
There was no truncation scenario at all.

Then I wrote `tests/test_audit_regressions.py` to pin every one of these fixes,
and one of the tests in it -- the one for the skipped-calls bug -- set
`usage.skipped = 17` by hand instead of driving the circuit breaker. It passed
whether the breaker counted skips or errors. I caught it only because I ran each
new test against a reverted copy of its own fix, and it was the single survivor
out of fifteen. Fourth instance, written while writing the file whose purpose is
to catch the first three.

The habit that catches this is not "write a test". It is "revert the fix and
watch the test fail". Seventeen of them now do.
