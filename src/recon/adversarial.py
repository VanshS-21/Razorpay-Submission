"""A holdout set the classifier was never designed against.

The main dataset cannot produce an honest accuracy number, because the same
person wrote the defects and the rules that detect them. Perfect scores there
measure internal consistency, not capability.

This module exists to break that. Every settlement here carries TWO defects at
once. The engine classifies with a single label and an ordered rule chain, so
compounds are outside its design by construction -- nothing here was reverse
engineered from the classifier, and the failures it produces are real.

Scored on DISPOSITION only (reconciled vs exception), never on reason code: with
two defects present there is no single correct reason code, and grading against
one would be meaningless in either direction.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from .generate import (
    GARBLED_NARRATIONS,
    NARRATION_TEMPLATES,
    Generator,
)
from .models import (
    AnomalyClass,
    BankCredit,
    Disposition,
    EntityType,
    GroundTruth,
)


class AdversarialGenerator(Generator):
    """Builds compound-defect settlements. Same primitives, unseen combinations."""

    def run(self, per_case: int = 4):
        builders = [
            self._missing_utr_plus_bank_charge,
            self._consolidated_plus_bank_charge,
            self._phantom_refund,
            self._duplicate_plus_ledger_error,
            self._chargeback_plus_shortfall,
        ]
        day = 0
        for build in builders:
            for _ in range(per_case):
                build(self.start + timedelta(days=day))
                day += 1
        self.bank.sort(key=lambda b: (b.value_date, b.txn_id))
        return self.lines, self.bank, list(self.orders.values()), self.truth

    # -- helpers -----------------------------------------------------------

    def _payments(self, setl_id, utr, d, n=None):
        n = n or self.rng.randint(4, 9)
        ls = [self._payment_line(setl_id, utr, d) for _ in range(n)]
        self.lines.extend(ls)
        return ls

    def _truth(self, sid, primary, disp, components, note):
        self.truth.append(GroundTruth(
            settlement_id=sid,
            true_class=primary,
            expected_disposition=disp,
            injected_delta=0,
            note=f"COMPOUND [{' + '.join(components)}]: {note}",
        ))

    def _charge_row(self, utr, d, amount):
        return BankCredit(
            txn_id=self._id("btxn"), value_date=d.isoformat(),
            narration="NEFT CHARGES INCL GST", ref_no=utr,
            debit=amount, credit=0,
        )

    # -- compounds ---------------------------------------------------------

    def _missing_utr_plus_bank_charge(self, d: date):
        """Unusable reference AND an itemised transfer charge.

        Amount-and-date fallback matching looks for the EXACT net. The charge
        moves the credit off that figure, so the fallback finds nothing and the
        payout looks as though it was never paid at all.
        """
        sid, utr = self._id("setl"), self._utr()
        ls = self._payments(sid, utr, d)
        net = sum(l.net for l in ls)
        charge = self.rng.choice([1770, 2360, 5900])
        self.bank.append(BankCredit(
            txn_id=self._id("btxn"), value_date=d.isoformat(),
            narration=self.rng.choice(GARBLED_NARRATIONS).format(
                partial=utr[:5], typo=utr[:4] + "XXXX"),
            ref_no="", debit=0, credit=net,
        ))
        self.bank.append(self._charge_row("", d, charge))
        self._truth(sid, AnomalyClass.MISSING_UTR, Disposition.RECONCILED,
                    ["missing_utr", "bank_charge_adjustment"],
                    "payout is fully explained; only its reference is unusable")

    def _consolidated_plus_bank_charge(self, d: date):
        """One transfer covering two payouts, minus a charge on the transfer.

        Subset-sum matches on exact totals. A charge levied on the consolidated
        transfer means no subset sums to the credit, so neither payout resolves.
        """
        pair = []
        for _ in range(2):
            sid, utr = self._id("setl"), self._utr()
            ls = self._payments(sid, utr, d, n=self.rng.randint(4, 7))
            pair.append((sid, utr, sum(l.net for l in ls)))
        (a_id, a_utr, a_net), (b_id, _, b_net) = pair
        charge = self.rng.choice([1770, 2360, 5900])
        self.bank.append(BankCredit(
            txn_id=self._id("btxn"), value_date=d.isoformat(),
            narration=self.rng.choice(NARRATION_TEMPLATES).format(utr=a_utr),
            ref_no=a_utr, debit=0, credit=a_net + b_net,
        ))
        self.bank.append(self._charge_row(a_utr, d, charge))
        for sid, other in ((a_id, b_id), (b_id, a_id)):
            self._truth(sid, AnomalyClass.CONSOLIDATED_PAYOUT, Disposition.RECONCILED,
                        ["consolidated_payout", "bank_charge_adjustment"],
                        f"jointly transferred with {other}, net of a transfer charge")

    def _phantom_refund(self, d: date):
        """A refund in the PSP report for an order the books never refunded.

        Money leaves the payout. The settlement still ties to the bank to the
        paise, because the refund is present on both sides. The only source that
        disagrees is the order ledger, which still shows the order as paid.

        This is the shape of a misposted or fraudulent refund, and it is exactly
        what a totals-based reconciliation is blind to.
        """
        sid, utr = self._id("setl"), self._utr()
        ls = self._payments(sid, utr, d)
        victim = self.rng.choice([o for o in self.orders.values()
                                  if o.status == "paid"])
        refund = self._refund_line(sid, utr, d, victim.order_id,
                                   victim.payment_id, victim.gross_amount,
                                   d - timedelta(days=9))
        # Critical: revert the ledger status the helper just set. The books must
        # still say "paid" -- that disagreement IS the defect.
        self.orders[victim.order_id].status = "paid"
        self.lines.append(refund)
        ls.append(refund)
        net = sum(l.net for l in ls)
        self.bank.append(BankCredit(
            txn_id=self._id("btxn"), value_date=d.isoformat(),
            narration=self.rng.choice(NARRATION_TEMPLATES).format(utr=utr),
            ref_no=utr, debit=0, credit=net,
        ))
        self._truth(sid, AnomalyClass.LEDGER_MISMATCH, Disposition.EXCEPTION,
                    ["refund_netted_later", "phantom_refund"],
                    f"refund debited for {victim.order_id}, which the order "
                    f"ledger still records as paid and never refunded")

    def _duplicate_plus_ledger_error(self, d: date):
        """Duplicate credit masking a line-level misstatement. Control case:
        either defect alone should escalate, so the compound must too."""
        sid, utr = self._id("setl"), self._utr()
        ls = self._payments(sid, utr, d)
        a, b = self.rng.sample(ls, 2)
        shift = self.rng.randint(5_000, 60_000)
        a.amount += shift; a.credit += shift
        b.amount -= shift; b.credit -= shift
        net = sum(l.net for l in ls)
        for i in range(2):
            self.bank.append(BankCredit(
                txn_id=self._id("btxn"),
                value_date=(d + timedelta(days=i)).isoformat(),
                narration=self.rng.choice(NARRATION_TEMPLATES).format(utr=utr),
                ref_no=utr, debit=0, credit=net,
            ))
        self._truth(sid, AnomalyClass.DUPLICATE_BANK_CREDIT, Disposition.EXCEPTION,
                    ["duplicate_bank_credit", "ledger_mismatch"],
                    "unearned duplicate credit over a misstated line")

    def _chargeback_plus_shortfall(self, d: date):
        """A genuine shortfall in a settlement that also carries a chargeback.

        Control case: the chargeback is a legitimate explanation for money
        leaving, and the risk is that the engine accepts it as the explanation
        for ALL the money leaving.
        """
        sid, utr = self._id("setl"), self._utr()
        ls = self._payments(sid, utr, d)
        victim = self.rng.choice([o for o in self.orders.values()])
        disp = self._dispute_line(sid, utr, d, victim.order_id,
                                  victim.payment_id, victim.gross_amount)
        self.lines.append(disp)
        ls.append(disp)
        net = sum(l.net for l in ls)
        gap = self.rng.randint(20_000, 200_000)
        self.bank.append(BankCredit(
            txn_id=self._id("btxn"), value_date=d.isoformat(),
            narration=self.rng.choice(NARRATION_TEMPLATES).format(utr=utr),
            ref_no=utr, debit=0, credit=net - gap,
        ))
        self._truth(sid, AnomalyClass.TRUE_MISMATCH, Disposition.EXCEPTION,
                    ["chargeback_deduction", "true_mismatch"],
                    "chargeback is real, but does not account for the shortfall")


def write_holdout(outdir: Path, seed: int = 1337, per_case: int = 4):
    from .generate import _write_csv

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    gen = AdversarialGenerator(seed=seed, n_settlements=0)
    lines, bank, orders, truth = gen.run(per_case=per_case)

    _write_csv(outdir / "settlement_recon.csv", lines, [
        "entity_id", "type", "debit", "credit", "amount", "currency", "fee", "tax",
        "settlement_id", "settlement_utr", "created_at", "settled_at",
        "payment_id", "order_id", "method", "description",
    ])
    _write_csv(outdir / "bank_statement.csv", bank, [
        "txn_id", "value_date", "narration", "ref_no", "debit", "credit"])
    _write_csv(outdir / "order_ledger.csv", orders, [
        "order_id", "order_date", "customer_id", "gross_amount", "currency",
        "status", "payment_id"])
    (outdir / "ground_truth.json").write_text(
        json.dumps([t.to_dict() for t in truth], indent=2), encoding="utf-8")
    return lines, bank, orders, truth


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/holdout")
    p.add_argument("--seed", type=int, default=1337)
    a = p.parse_args()
    l, b, o, t = write_holdout(Path(a.out), a.seed)
    print(f"wrote {len(t)} compound settlements / {len(l)} lines -> {a.out}/")
