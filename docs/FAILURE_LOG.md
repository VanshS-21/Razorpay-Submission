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
