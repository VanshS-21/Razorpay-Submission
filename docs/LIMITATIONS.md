# Known limitations

The five that matter most are in the README. These are the rest. Each is a real
constraint on what the engine can be trusted to do, stated so a reader does not
have to discover it.

## Single-label classification

A settlement carrying two defects gets one reason code. The disposition stays
correct — it escalates — but the explanation is partial. This is why the
adversarial holdout is scored primarily on disposition, and why its reason-code
figure is published beside an exact-primary figure rather than alone.

## Charge attribution needs an unambiguous day

An unreferenced bank charge is attached to a settlement only when its value date
carries exactly one. Both generators emit one payout a day, so the rule always
fires in testing. A real merchant has dozens of payouts a day, and it never will.
The charge then stays in the orphan list and is reported rather than attributed,
which is the safe direction, but the holdout's `missing_utr + bank_charge` family
measures less than it appears to.

## Contested consolidations clear nothing

When two over-credited settlements could each be explained by the same unpaid
payout, neither is cleared and both escalate. One payout can only pay for one
thing, and nothing in the data says which. The consequence is that a busy
statement escalates more than a quiet one for reasons about ambiguity rather than
about error.

## One currency

`currency` is read from all three sources, and anything other than INR is refused
at ingest with a message naming the row. Every total here assumes a single unit.
Reconciling across two without a rate would add numbers that are not the same
kind of thing. Multi-currency merchants are out of scope.

## Money units are inferred per file

A decimal point anywhere in a money column means the file is written in rupees; a
file with none is read as paise. Real exports are internally consistent about
this. A file that mixes both conventions is genuinely ambiguous and will be read
wrong — loudly, because a whole file scaled by 100 does not balance, rather than
quietly.

This replaced a per-cell guess, under which `16.95` and `17` in the same column
one row apart were read as Rs 16.95 and Rs 0.17. That failure was silent: it
scaled every column of a row by the same factor, so the settlement stayed
internally consistent and reconciled clean at 1% of its true value.

## Cost accounting uses floats

The reconciliation path is integer paise throughout, with no exceptions. The
USD/INR estimate in `agent/llm.py` is not, and it never touches a settlement.

## Bank charges are recognised by narration keywords

`CHARGE`, `COMMISSION`, `FEE`. A bank wording its fees differently gets its
shortfalls escalated as unexplained, which is safe but noisy. Production should
match against the bank's published fee schedule rather than a word list.
