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
