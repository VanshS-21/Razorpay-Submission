# Video script — working draft

Five minutes, unlisted. Built in the order we're learning, so each section
unlocks on the day that covers it.

**Status key**

- ✅ **READY** — covered, you can deliver this today
- 🔒 **DAY n** — locked until that day's session

**Do not memorise this.** It is a scaffold, not a recital. An interviewer can
tell the difference in about ten seconds, and a memorised script collapses the
moment you lose your place. Learn the *ideas*; the words can come out differently
every time.

---

## Timing budget

| Section | Time | Status |
|---|---|---|
| 1. The problem | 0:00 – 0:45 | ✅ READY |
| 2. Why it's hard | 0:45 – 1:40 | ✅ READY |
| 3. How money is stored | 1:40 – 2:00 | ✅ READY |
| 4. Show it running | 2:00 – 2:40 | ✅ READY |
| 5. Where the AI is, and isn't | 2:40 – 3:25 | ✅ READY |
| 6. How I know the numbers are real | 3:25 – 4:00 | 🔒 DAY 4 |
| 7. What broke | 4:00 – 4:45 | 🔒 DAY 4 |
| 8. Close | 4:45 – 5:00 | 🔒 DAY 5 |

Roughly 700–750 spoken words. **Five of eight sections are now deliverable.**

---

## 1. The problem — ✅ READY

*[SCREEN: just you, or a title card]*

> Hi, I'm ⟨name⟩, second year at ⟨college⟩. For Track 4 I built a three-way
> settlement reconciliation engine.
>
> When you buy something online for ₹500, the shop does not get ₹500. Razorpay
> takes a 2% fee, plus 18% GST on that fee — so the shop gets ₹488.20.
>
> But it doesn't get ₹488.20 either. Not on its own. Razorpay collects payments
> all day, waits a day or two, and sends **one** bank transfer covering the whole
> batch.
>
> So on Wednesday morning, the shop's finance person opens the bank statement and
> sees a single line. One date, one blob of text, one number — say ₹3,905.60.
>
> Their job is to decide whether that number is correct.

**Delivery note:** slow down on the last line. That's the hook.

---

## 2. Why it's hard — ✅ READY

*[SCREEN: the five payouts and the ₹950 credit]*

> To check that number you need three documents, each from a different party.
> Your own order ledger. Razorpay's settlement report. The bank statement. That's
> what "three-way" means.
>
> Here's the part I didn't expect when I started.
>
> That one credit isn't *for* one payout. It covers some **set** of payouts. So
> the question "which payout is this credit for?" has no answer at all. The right
> question is "which **subset** does it cover?" — and that's a search, not a
> lookup.
>
> And a search can come back with more than one answer.
>
> Five unpaid payouts: ₹100, ₹250, ₹400, ₹550, ₹700. A credit of ₹950 arrives.
> That's ₹400 plus ₹550. It's *also* ₹250 plus ₹700. Both valid. Nothing in the
> arithmetic says which.
>
> My engine refuses to decide. Two answers means no answer, so it escalates to a
> human. Because if it guessed wrong, nothing would announce the error. The books
> would say "reconciled." Nobody would be looking. And every reconciliation after
> that would start from a position that's already wrong.

**Delivery note:** this section decides whether they keep watching. It is
entirely yours — you derived all of it. Say it like you mean it.

---

## 3. How money is stored — ✅ READY

*[SCREEN: `python -c "print(0.1 + 0.2)"` and its output]*

> Money is stored as whole paise, never decimals. ₹488.20 is the integer 48820.
>
> Because in a computer, 0.1 plus 0.2 is 0.30000000000000004. My matching is
> exact, so even that much drift breaks it.
>
> And the obvious fix is a trap. Forgive small differences to cope with drift, and
> you've built a hiding place — someone skimming 50 paise a transaction becomes
> invisible, because you can't tell a rounding artefact from theft any more.

**Delivery note:** ~20 seconds. Supporting point, not headline. Don't linger.

---

## 4. Show it running — ✅ READY

*[SCREEN: terminal, then `out/report.html`]*

Screen-record this with voice over. Do a dry run first so nothing errors on
camera.

- `python demo.py` — let it scroll. Four stages: generate, main set, holdout,
  write the report.
- Point at the **false-clear rate first**, then match rate. Say why that order.
- Open `out/report.html`. Show the exception list, and read one exception's
  action line aloud — that's what a human actually receives.
- Show **bank-side coverage**: 30 statement rows the engine could not attach to
  any payout, reported rather than discarded.

> ⟨narrate what you're pointing at — don't read the screen aloud⟩

**Numbers to have straight:** 126 settlements, 1,149 lines, 0 false clears out of
19 must-escalate, 84.9% match rate, 0.04 seconds.

---

## 5. Where the AI is, and where it isn't — ✅ READY

*This is the "AI judgment" criterion. They explicitly want to see where you chose
NOT to use a model.*

> The rule is: deterministic where money is concerned, probabilistic only where
> language is.
>
> The model never touches arithmetic and cannot move a verdict. It writes the
> exception notes, and it can suggest which settlement an unreadable bank
> reference belongs to — but a suggestion is a *lead*, attached to the exception
> for the human. It's never handed to the classifier, so there's no code path by
> which it could clear a payout.
>
> To prove that isn't just a claim, I put two vendors — Anthropic and Gemini —
> behind one interface. You can switch with a flag. **The verdicts don't move.**
> The model is a replaceable part.
>
> And the honest finding: the model earned less than I expected. I built the
> garbled-reference case expecting it to be the thing that justified using a
> model. Deterministic matching resolves 100% of them on its own. I didn't
> respond by making the data harder to justify the architecture — I reported it.

**Optional, if the pacing allows — the strongest single line in this section:**

> A full batch needs 19 model calls, and Gemini's free tier allows 20 a day. The
> same batch reconciles in **0.04 seconds with zero model calls** and identical
> verdicts. The model is a garnish. The books close without it.

---

## 6. How I know the numbers are real — 🔒 DAY 4

Points to hit:

- The generator writes the fake data **and** the answer key, so accuracy is
  checkable rather than asserted.
- False-clear rate first, because it's the expensive error.
- **The uncomfortable bit:** 100% on the main set is nearly worthless — you wrote
  both the defects and the detector. So you built a second, harder dataset the
  engine was never designed against, where every settlement carries two defects
  at once.
- And the caveat that costs you something to say: **fixing bugs against a holdout
  turns it into training data.** It can't be reused as a clean holdout, and the
  README says so where the numbers appear.

> ⟨fill in after Day 4⟩

---

## 7. What broke — 🔒 DAY 4

*The form asks about this and the site says they read it first. Your strongest
material. The arc is: I learned a lesson, then made the same mistake again five
days later — and caught it because I'd learned to look.*

**Beat one — the audit.**

- You commissioned an adversarial audit of your own project: an agent told to
  assume nothing in the documentation was true.
- It broke the claim you were proudest of — *"whatever the model does, not one
  verdict moves."*
- The guard checking the model re-applied the same test the matcher had already
  run. That's circular: rows reach the model precisely *because* arithmetic
  couldn't identify them, so every candidate passes.
- And the test meant to catch it **could not fail** — the code path it guarded
  never executed on the shipped data.

**Beat two — the same lesson, five days later.**

- Wiring in a second vendor, you found 30 of 49 model calls were being spent
  asking about settlements that were **already reconciled** by subset-sum. 61% of
  the cost, on questions with no possible answer.
- Fixing that waste made a test go **vacuous** — no proposals left to reject — and
  the tempting fix was to soften the assertion.
- You rebuilt the test's input instead, so it genuinely runs.

**The line to land on:**

> A test over a path that can't execute isn't weak evidence — it's *no* evidence,
> and it's worse than no test, because it reads like proof. I've now written that
> bug twice. The habit I'm building: after making a test pass, ask whether it
> *could* have failed.

**If you want a third, smaller one:** the free-tier quota exposed a cost figure
9.5× too low — a real measurement, correctly computed, wearing a label that
didn't describe it.

> ⟨fill in after Day 4⟩

---

## 8. Close — 🔒 DAY 5

Points to hit:

- One honest limitation, plainly. (An over-credit sitting beside an unrelated
  unpaid payout of exactly the right size is *arithmetically identical* to a real
  consolidated transfer. No rule separates them, so it's cleared but flagged for
  spot-check rather than quietly hidden.)
- What you'd do with more time.
- Thanks, and where the repo is.

> ⟨fill in after Day 5⟩

---

# Rules for recording

**Do not say any word you cannot define.** If a word is in this script and you
can't explain it cold, either learn it or cut the sentence. A confident sentence
you can't defend is the worst thing you can put in this video.

Watch-list — you should be able to define all of these by the 4th:

`net` · `settlement` · `UTR` · `reconciliation` · `subset-sum` · `false clear` ·
`false escalate` · `paise` · `basis points` · `ground-truth key` · `holdout` ·
`disposition` · `reason code` · `guard` · `deterministic` · `expected_net` ·
`observed_net` · `delta` · `orphan bank row` · `provider` · `thinking tokens` ·
`structured output` · `lead` · `mutation testing`

All twenty-four are in [`GLOSSARY.md`](GLOSSARY.md).

**Explain the reasoning, not the code.** Nobody wants five minutes of scrolling
through files. They want to know why the problem is hard and how you thought
about it.

**Don't over-polish.** Slightly rough and clearly yours beats slick and
memorised. Two or three takes, not twenty.

**On AI assistance.** It's an *AI* Buildathon — using Claude Code is the point,
not something to hide. If it comes up: you directed and reviewed the build, and
you commissioned an independent audit against your own work. What you must never
do is claim authorship of something you can't explain — which is the entire
reason for this week.

---

## Progress

- [x] Day 1 — sections 1, 2, 3
- [x] Day 2 — section 4 (five-stage trace, delta, three sources)
- [x] Day 3 — section 5 (vendor boundary, measured against a real model)
- [ ] Day 4 — sections 6, 7
- [ ] Day 5 — section 8, record

*Working document. Not part of the submission — delete before pushing, or keep
it, your call.*
