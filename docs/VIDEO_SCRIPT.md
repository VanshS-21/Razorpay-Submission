# Video script — 5 minutes, unlisted

Track 04, AI Finance Controller. Pairs with [`slides.html`](slides.html) — **21
slides, one section each below.** Open the deck, press `F` for fullscreen, and
press → when you reach the next heading.

**Every bullet is one thing you say.** Say them in order and the slide is done.

**The last bullet of each section is marked ↳.** That's the bridge — say it
*while the current slide is still up*, then press →. It carries the listener
across the cut so nothing lands cold. It's the difference between a talk and a
slideshow.

**Lines marked ✂ go first.** Read in full it's about 5:10 of talking, which
leaves no room to breathe. Cutting all the ✂ lines brings it to roughly 4:35 and
gives you ~25 seconds of pause across 21 slides. Record it long once, hear where
you run over, then cut.

**One take per beat, not one take for the video.**

---

## 1 · Bank statement row — 0:00

- This is one line on a merchant's bank statement.
- One credit, one amount.
- Someone in the finance team has to work out whether it's right.
- ↳ And you'd think that's a simple question.

*Open cold. No name yet — that comes at the end.*

## 2 · "That is not a payment" — 0:10

- It isn't, because that's not a payment.
- It's one lump sum covering dozens of payments, minus fees, minus GST, minus
  refunds and chargebacks.
- Sometimes it's several payouts swept into one transfer, quoting a single
  reference number.
- ↳ Which breaks the question you were about to ask.

## 3 · Which group, not which one — 0:22

- Because *which settlement is this credit for* doesn't have an answer.
- The real question is which **group** of settlements it covers.
- And that's something you search for, not something you look up.
- ✂ Which is why most merchants still do this by hand.
- ↳ So — how do you actually check it?

---

## 4 · Three sources — 0:35

- You need three documents, and they come from three different places.
- What Razorpay says it paid out. What actually landed in the bank. And what the
  business thinks it sold.
- They should match. They never do.
- ↳ So this reconciles all three.

## 5 · `python demo.py` — 0:45

- One command. No API key, no network, nothing installed beyond the standard
  library.
- A hundred and twenty-six settlements, about eleven hundred lines, in roughly
  five hundredths of a second.
- ↳ *(pause two seconds)* And these are the numbers it gives you.

---

## 6 · The SAFETY block — 1:15

- This one first.
- A false clear is when the engine says "this one's fine" and it wasn't.
- Zero out of nineteen. And zero false escalates out of a hundred and seven.
- ✂ That's the expensive mistake — money quietly going missing. Flagging
  something unnecessarily just costs someone ten minutes.
- ↳ Then the coverage.

## 7 · Coverage — 1:35

- Match rate is just under eighty-five percent.
- The other fifteen percent isn't failure — it's nineteen settlements that
  genuinely need a person.
- ✂ Plus thirty bank rows it couldn't tie to any payout, and reports rather than
  guesses at.
- ↳ And one more number, which I want you to be suspicious of.

## 8 · The hook — 1:52

- Reason-code accuracy: a hundred percent.
- I'll come back to why you shouldn't believe that.
- ↳ First, the case I'd actually point at.

---

## 9 · The exception — 2:05

- Two payment lines disagree with the order ledger.
- One order's booked at six hundred and twenty-four rupees, and the ledger says
  five fifty-seven.
- But the errors cancel out across the settlement.
- ↳ Which does something strange to the bottom line.

## 10 · Delta zero — 2:20

- The delta is zero. Every total balances.
- The payout ties, and the bank credit ties.
- If you're only checking the bank against the payout, you can't see this at
  all. You need the third source.
- ↳ *(slow down here)* And this is what a person actually receives.

## 11 · The action line — 2:35

- Not "mismatch". It tells them what to go and do.
- And why the total balancing doesn't mean the lines are right.
- ↳ That note is the one place in this whole system where a model is involved.

---

## 12 · The rule — 2:45

- Because my rule was that anything touching money is plain code and plain
  arithmetic.
- The model only gets used where the problem is actually language.
- ↳ In practice, that's a hard line.

## 13 · The boundary — 2:57

- It can't compute an amount. It can't decide whether a settlement is
  reconciled. It can't assign a reason code.
- What it *does* do is write the nineteen exception notes a human has to act on.
- ✂ Money's stored as whole paise the whole way through. No floats anywhere
  near it.
- ↳ And I don't trust it to do even that much unchecked.

*Say the "did not" half out loud. Where a model is absent is a decision, and
it is worth as much airtime as where one is present.*

## 14 · The guard — 3:12

- So this is a fake client that invents a rupee figure in every single note.
- Nineteen out of nineteen rejected. And not one verdict moved.
- ↳ That's a model failing on purpose. Here's what real ones did.

## 15 · Four models, two vendors — 3:30

- Four live models, two vendors — Google and AWS.
- Five hundred and four verdict fields compared. Zero moved.
- ✂ One of those models failed eighteen of its nineteen calls, and the books
  still closed identically.
- ↳ Which brings me back to that hundred percent.

---

## 16 · Back to the 100% — 3:45

- I wrote the thing that creates the problems, and I wrote the thing that finds
  them.
- So all that number tells you is that two versions of my own assumptions agree.
- ↳ So I tried to break it.

## 17 · The holdout failing — 3:58

- A second dataset, with two things wrong in every settlement.
- It failed straight away. Thirty-three percent false clears — four out of
  twelve.
- Both causes were real bugs. I'd written a three-way reconciler that only did
  three-way in the direction money comes in.
- ↳ That wasn't the worst one, though.

## 18 · The claim that was false — 4:15

- The claim I was proudest of turned out to be false. My guard was re-running a
  test the matcher had already failed, so everything passed it.
- ✂ And the test protecting that couldn't fail either.
- Both fixed, both pinned with tests. The log has all of it in the order it
  happened, with the wrong paragraphs left in.
- ↳ And there's one more I'll say myself, before you have to find it.

*Don't apologise while you say this. You're showing them how you work.*

---

## 19 · The limitation — 4:35

- Once I fixed the engine using that holdout, it stopped being unseen data. It's
  training data now.
- The README says that right next to the number.
- ↳ What I can tell you is that everything here is checkable.

## 20 · Checkable — 4:47

- Two hundred and two tests, no key and no network.
- And a script that checks the README's numbers against a live run — so if I've
  overstated anything in there, it fails.
- ↳ That's it.

## 21 · Close — 4:55

- I'm Vansh. It's all on GitHub, and it runs on a fresh clone with one command.
- Thanks for watching.

---

# Recording notes

**The deck is the whole video.** Open `docs/slides.html`, press `F`, press → at
each heading. You never alt-tab, never scroll, never look for a file.

**Say the ↳ line before you advance, not after.** That's what makes it a bridge
rather than a caption. If you press → first, the new slide is already up and the
sentence sounds like a late explanation.

**Optional:** film `python demo.py` running for real, between slides 4 and 5.
Slide 5 already shows its output, so skip it if screen-switching is a hassle.

**The helper bar auto-hides** after 2.4 seconds, so it won't appear in your
recording. Move the mouse to check pacing; the clock turns red when you're more
than 5 seconds past a slide's target.

**Don't read the slide aloud.** It's on screen. Say the thing the slide doesn't.

**Don't over-polish.** Slightly rough and clearly yours beats slick and
memorised. Leave a stumble in.

**If you overrun, cut beats 6–8** (the numbers). They're in the README. Keep
slides 9–11, 14, and 16–18 whole — the delta-zero case, the guard, and the
failure log are the three that carry the most weight.

**If AI assistance comes up:** it's an AI buildathon, using Claude Code is the
point. You directed the build, you reviewed it, and you had it audited against
your own work. The one thing you must never do is claim you wrote something you
can't explain.

---

## Before you upload

- [ ] Open `docs/slides.html`, press `F`, press `R` to reset the clock
- [ ] Recorded — one take per beat
- [ ] Uploaded **unlisted**
- [ ] URL pasted into `README.md` line 10, replacing the italic placeholder
- [ ] Google Form submitted — **5 September**

*Working document. Not part of the submission.*
