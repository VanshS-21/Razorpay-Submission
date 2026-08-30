"""Synthetic three-source dataset generator with a ground-truth answer key.

This is the most important file in the project. Everything downstream is scored
against the key it emits, so the key -- not the agent -- is what makes the
accuracy numbers in the README falsifiable by anyone who clones the repo.

It writes four files:
    settlement_recon.csv   PSP side  (what Razorpay says it paid out)
    bank_statement.csv     bank side (what actually landed in the account)
    order_ledger.csv       books side(what the merchant thinks it sold)
    ground_truth.json      the key   (what is really going on, per settlement)

Anomaly rates are deliberately inflated relative to production. A real merchant
sees 1-2% exceptions; at that rate a 60-settlement batch would contain one
true mismatch and precision/recall on the classes that matter would be noise.
The weights below oversample the rare-but-expensive classes so the metrics have
real support. This is stated in the README rather than hidden.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from .models import (
    AnomalyClass,
    BankCredit,
    EntityType,
    GroundTruth,
    OrderRecord,
    SettlementLine,
    expected_disposition,
)

# --------------------------------------------------------------------------
# Pricing constants (Razorpay standard-ish: 2% fee, 18% GST on the fee)
# All arithmetic is integer paise. `//` truncation is the actual bank behaviour.
# --------------------------------------------------------------------------

FEE_BPS = 200          # 2.00%
GST_BPS = 1800         # 18% on the fee
DISPUTE_FEE = 150000   # Rs 1,500 flat chargeback handling fee

METHODS = ["upi", "card", "netbanking", "wallet", "emi"]

#: Relative frequency of each injected class. See module docstring on inflation.
DEFAULT_WEIGHTS: dict[AnomalyClass, int] = {
    AnomalyClass.CLEAN: 40,
    AnomalyClass.FEE_TAX_ROUNDING: 9,
    AnomalyClass.REFUND_NETTED_LATER: 7,
    AnomalyClass.CHARGEBACK_DEDUCTION: 7,
    AnomalyClass.MISSING_UTR: 8,
    AnomalyClass.TIMING_CUT: 7,
    AnomalyClass.DUPLICATE_BANK_CREDIT: 7,
    AnomalyClass.SPLIT_REFUND: 5,
    AnomalyClass.BANK_CHARGE_ADJUSTMENT: 4,
    AnomalyClass.CONSOLIDATED_PAYOUT: 6,
    AnomalyClass.LEDGER_MISMATCH: 6,
    AnomalyClass.TRUE_MISMATCH: 6,
}

NARRATION_TEMPLATES = [
    "NEFT-{utr}-RAZORPAY SOFTWARE PVT LTD-HDFC0000060",
    "IMPS/{utr}/RAZORPAY/SETTLEMENT",
    "RTGS-{utr}-RAZORPAY SOFTWARE PRIVATE LIMITED",
    "NEFT CR-HDFC0000060-RAZORPAY SOFTWARE PVT LTD-{utr}",
]

#: Narrations for the MISSING_UTR class: the reference field is unusable, so the
#: only signal left is prose. This is the one place a language model genuinely
#: outperforms a regex, which is why the class exists.
GARBLED_NARRATIONS = [
    "NEFT CR-RAZORPAY SOFTWARE PVT LTD-SETTLEMENT PAYOUT",
    "IMPS/RAZORPAYSOFTWARE/PAYOUT/REF UNAVAILABLE",
    "NEFT-RZRPY SOFT PVT-STLMNT-{partial}",
    "FUND TRF FRM RAZORPAY SOFTWARE PVT LTD",
    "NEFT CR-{typo}-RAZORPAY SOFTWARE PVT LTD",
]


def fee_for(amount: int) -> tuple[int, int]:
    """Return (fee, tax) in paise for a gross amount in paise."""
    fee = amount * FEE_BPS // 10_000
    tax = fee * GST_BPS // 10_000
    return fee, tax


def allocate(n: int, weights: dict, floor: int = 5) -> list:
    """Apportion n settlements across classes by weight, with a per-class floor.

    Drawing classes randomly leaves the rare ones with 1-2 instances, and a
    precision figure computed over two samples is not a measurement. Stratified
    allocation guarantees every class enough support to be scored, and removes
    sampling noise between runs so a change in the metrics reflects a change in
    the engine rather than a change in the draw.
    """
    classes = list(weights)
    total_w = sum(weights.values())

    # Largest-remainder apportionment.
    raw = {c: n * weights[c] / total_w for c in classes}
    counts = {c: int(raw[c]) for c in classes}
    for c in sorted(classes, key=lambda c: raw[c] - int(raw[c]), reverse=True):
        if sum(counts.values()) >= n:
            break
        counts[c] += 1

    # Raise anything under the floor, taking from the largest bucket that can
    # afford it. Skipped entirely when n is too small for the floor to fit.
    if floor * len(classes) <= n:
        for c in classes:
            while counts[c] < floor:
                donor = max(classes, key=lambda x: counts[x])
                if counts[donor] <= floor:
                    break
                counts[donor] -= 1
                counts[c] += 1

    out = []
    for c in classes:
        out.extend([c] * counts[c])
    return out


class Generator:
    """Builds a coherent three-source dataset with known, labelled defects."""

    def __init__(self, seed: int = 42, n_settlements: int = 60,
                 weights: dict | None = None, start: date | None = None):
        self.rng = random.Random(seed)
        self.n_settlements = n_settlements
        self.weights = weights or DEFAULT_WEIGHTS
        self.start = start or date(2026, 6, 1)

        self.lines: list[SettlementLine] = []
        self.bank: list[BankCredit] = []
        self.orders: dict[str, OrderRecord] = {}
        self.truth: list[GroundTruth] = []

        self._seq = 0
        # Orders from earlier settlements, available for late refunds/disputes.
        self._past_orders: list[tuple[str, str, int]] = []   # (order_id, payment_id, amount)
        # Second halves of split refunds, to be dropped into a later settlement.
        self._pending_splits: list[tuple[str, str, int]] = []

    # -- id helpers --------------------------------------------------------

    def _id(self, prefix: str) -> str:
        self._seq += 1
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
        tail = "".join(self.rng.choice(alphabet) for _ in range(10))
        return f"{prefix}_{tail}{self._seq:04d}"

    def _utr(self) -> str:
        return f"{self.rng.randint(10**9, 10**10 - 1)}{self.rng.choice('abcdefghijklmnopqrstuvwxyz')}{self.rng.randint(100, 999)}"

    def _amount(self) -> int:
        """A plausible order value in paise, skewed small like real commerce."""
        bucket = self.rng.random()
        if bucket < 0.55:
            return self.rng.randint(9_900, 250_000)          # Rs 99 - 2,500
        if bucket < 0.9:
            return self.rng.randint(250_000, 2_000_000)      # Rs 2.5k - 20k
        return self.rng.randint(2_000_000, 15_000_000)       # Rs 20k - 1.5L

    # -- line builders -----------------------------------------------------

    def _payment_line(self, setl_id: str, utr: str, d: date) -> SettlementLine:
        amount = self._amount()
        fee, tax = fee_for(amount)
        order_id = self._id("order")
        payment_id = self._id("pay")
        created = d - timedelta(days=2)

        self.orders[order_id] = OrderRecord(
            order_id=order_id,
            order_date=created.isoformat(),
            customer_id=self._id("cust"),
            gross_amount=amount,
            currency="INR",
            status="paid",
            payment_id=payment_id,
        )
        self._past_orders.append((order_id, payment_id, amount))

        return SettlementLine(
            entity_id=payment_id,
            type=EntityType.PAYMENT,
            debit=0,
            credit=amount - fee - tax,
            amount=amount,
            currency="INR",
            fee=fee,
            tax=tax,
            settlement_id=setl_id,
            settlement_utr=utr,
            created_at=created.isoformat(),
            settled_at=d.isoformat(),
            payment_id=payment_id,
            order_id=order_id,
            method=self.rng.choice(METHODS),
            description="Payment captured",
        )

    def _refund_line(self, setl_id: str, utr: str, d: date, order_id: str,
                     payment_id: str, amount: int, created: date,
                     partial: bool = False) -> SettlementLine:
        if order_id in self.orders:
            self.orders[order_id].status = "partially_refunded" if partial else "refunded"
        return SettlementLine(
            entity_id=self._id("rfnd"),
            type=EntityType.REFUND,
            debit=amount,
            credit=0,
            amount=amount,
            currency="INR",
            fee=0,
            tax=0,
            settlement_id=setl_id,
            settlement_utr=utr,
            created_at=created.isoformat(),
            settled_at=d.isoformat(),
            payment_id=payment_id,
            order_id=order_id,
            method=None,
            description="Partial refund" if partial else "Full refund",
        )

    def _dispute_line(self, setl_id: str, utr: str, d: date, order_id: str,
                      payment_id: str, amount: int) -> SettlementLine:
        return SettlementLine(
            entity_id=self._id("disp"),
            type=EntityType.DISPUTE,
            debit=amount + DISPUTE_FEE,
            credit=0,
            amount=amount,
            currency="INR",
            fee=DISPUTE_FEE,
            tax=0,
            settlement_id=setl_id,
            settlement_utr=utr,
            created_at=(d - timedelta(days=self.rng.randint(20, 60))).isoformat(),
            settled_at=d.isoformat(),
            payment_id=payment_id,
            order_id=order_id,
            method=None,
            description="Chargeback deduction incl. handling fee",
        )

    # -- main loop ---------------------------------------------------------

    def _add_distractors(self, n: int):
        """Bank rows that have nothing to do with Razorpay settlements.

        A real current account carries payroll, vendor payments, other payment
        gateways and customer transfers. Without them the statement is a list of
        answers and the matcher is never tested on its willingness to leave a row
        alone. Some of these deliberately mention Razorpay, so rejecting them
        cannot be done on the merchant name alone.
        """
        templates = [
            ("NEFT CR-ICIC0000123-CASHFREE PAYMENTS INDIA PVT LTD-{ref}", "credit"),
            ("IMPS/{ref}/PHONEPE PAYMENT SERVICES PVT LTD", "credit"),
            ("UPI-{ref}-CUSTOMER DIRECT TRANSFER", "credit"),
            ("NEFT DR-{ref}-VENDOR PAYMENT-SUPPLIES", "debit"),
            ("SALARY DISBURSEMENT JUN2026 BATCH {ref}", "debit"),
            ("NEFT DR-RAZORPAY SOFTWARE PVT LTD-{ref}-SUBSCRIPTION FEE", "debit"),
            ("NEFT CR-RAZORPAY SOFTWARE PVT LTD-{ref}-INSTANT SETTLEMENT ADVANCE", "credit"),
            ("ACH DR-GST PAYMENT-{ref}", "debit"),
        ]
        base = self.start
        for _ in range(n):
            tmpl, kind = self.rng.choice(templates)
            ref = f"{self.rng.randint(10**9, 10**10 - 1)}x{self.rng.randint(100, 999)}"
            amt = self.rng.randint(50_000, 8_000_000)
            d = base + timedelta(days=self.rng.randint(0, max(1, self.n_settlements - 1)))
            self.bank.append(BankCredit(
                txn_id=self._id("btxn"),
                value_date=d.isoformat(),
                narration=tmpl.format(ref=ref),
                ref_no=ref,
                debit=amt if kind == "debit" else 0,
                credit=amt if kind == "credit" else 0,
            ))

    def run(self):
        schedule = allocate(self.n_settlements, self.weights)
        self.rng.shuffle(schedule)

        for i, cls in enumerate(schedule):
            d = self.start + timedelta(days=i)
            if cls is AnomalyClass.CONSOLIDATED_PAYOUT:
                self._build_consolidated(d)
            else:
                self._build_settlement(self._id("setl"), self._utr(), d, cls)

        # Roughly one unrelated row for every four settlement payouts.
        self._add_distractors(max(4, self.n_settlements // 4))
        self.bank.sort(key=lambda b: (b.value_date, b.txn_id))

        return self.lines, self.bank, list(self.orders.values()), self.truth

    def _build_consolidated(self, d: date):
        """Two settlements, one bank transfer.

        The bank swept two same-window payouts into a single NEFT and quoted only
        the first UTR. Joining on that UTR finds settlement A but the amount is
        wrong by exactly the size of settlement B, and settlement B looks like it
        was never paid at all. Both units are individually unexplainable; only
        recognising that one credit covers a SUBSET of settlements resolves them.
        This is the case that makes a subset-sum solver necessary rather than
        decorative.
        """
        pair = []
        for _ in range(2):
            setl_id, utr = self._id("setl"), self._utr()
            lines = [self._payment_line(setl_id, utr, d)
                     for _ in range(self.rng.randint(4, 10))]
            self.lines.extend(lines)
            pair.append((setl_id, utr, sum(l.net for l in lines)))

        (a_id, a_utr, a_net), (b_id, b_utr, b_net) = pair

        self.bank.append(BankCredit(
            txn_id=self._id("btxn"),
            value_date=d.isoformat(),
            narration=self.rng.choice(NARRATION_TEMPLATES).format(utr=a_utr),
            ref_no=a_utr,               # only ONE of the two UTRs is quoted
            debit=0,
            credit=a_net + b_net,       # ...but the money covers both
        ))

        for sid, other in ((a_id, b_id), (b_id, a_id)):
            self.truth.append(GroundTruth(
                settlement_id=sid,
                true_class=AnomalyClass.CONSOLIDATED_PAYOUT,
                expected_disposition=expected_disposition(AnomalyClass.CONSOLIDATED_PAYOUT),
                injected_delta=0,
                note=f"Paid in one bank transfer jointly with {other}",
            ))

    def _build_settlement(self, setl_id: str, utr: str, d: date, cls: AnomalyClass):
        lines: list[SettlementLine] = []
        note = ""
        injected = 0

        # Snapshot before adding this batch: late refunds and chargebacks must
        # reference an order from an EARLIER settlement, which is precisely what
        # makes them hard to tie back.
        prior_orders = list(self._past_orders)

        # Every settlement has a body of ordinary captured payments.
        for _ in range(self.rng.randint(4, 14)):
            lines.append(self._payment_line(setl_id, utr, d))

        # Drain split-refund second halves ONLY into settlements already labelled
        # SPLIT_REFUND. Letting them fall into a CLEAN settlement would put a
        # partial refund inside a unit whose answer key says "nothing unusual
        # here", quietly corrupting the very thing the key exists to guarantee.
        if cls is AnomalyClass.SPLIT_REFUND and self._pending_splits:
            for order_id, payment_id, amt in self._pending_splits:
                lines.append(self._refund_line(setl_id, utr, d, order_id, payment_id,
                                               amt, d - timedelta(days=1), partial=True))
            self._pending_splits.clear()

        # --- class-specific construction ---------------------------------

        if cls is AnomalyClass.FEE_TAX_ROUNDING:
            # The PSP rounds fee and GST per line; the bank moves one lump sum
            # computed on the aggregate. The two disagree by a few paise. This is
            # normal and must NOT create an exception -- but it is also the exact
            # shape of a real discrepancy, just smaller, so the tolerance that
            # absorbs it has to be justified rather than eyeballed.
            for ln in self.rng.sample(lines, min(3, len(lines))):
                d_paise = self.rng.choice([-2, -1, 1, 2])
                ln.tax += d_paise
                ln.credit -= d_paise
            self._rounding_drift = self.rng.choice([-3, -2, -1, 1, 2, 3])
            note = "Sub-rupee fee/GST rounding drift between per-line and aggregate totals"

        elif cls is AnomalyClass.REFUND_NETTED_LATER:
            if prior_orders:
                oid, pid, amt = self.rng.choice(prior_orders)
                created = d - timedelta(days=self.rng.randint(5, 25))
                lines.append(self._refund_line(setl_id, utr, d, oid, pid, amt, created))
                note = f"Refund for order {oid} raised {created} but netted into this payout"

        elif cls is AnomalyClass.CHARGEBACK_DEDUCTION:
            if prior_orders:
                oid, pid, amt = self.rng.choice(prior_orders)
                lines.append(self._dispute_line(setl_id, utr, d, oid, pid, amt))
                note = f"Chargeback on {oid} plus Rs 1,500 handling fee deducted from payout"

        elif cls is AnomalyClass.LEDGER_MISMATCH:
            # Two payment lines are wrong by equal and opposite amounts. The
            # settlement total is untouched, the bank credit ties to the paise,
            # and every single-axis check passes. Only comparing each line back
            # to the order ledger finds it.
            if len(lines) >= 2:
                a, b = self.rng.sample([l for l in lines
                                        if l.type is EntityType.PAYMENT], 2)
                shift = self.rng.randint(5_000, 80_000)   # Rs 50 - Rs 800
                a.amount += shift
                a.credit += shift
                b.amount -= shift
                b.credit -= shift
                note = (f"Orders {a.order_id} and {b.order_id} misstated by "
                        f"{shift} paise in opposite directions; settlement total "
                        f"unaffected")

        elif cls is AnomalyClass.SPLIT_REFUND:
            if prior_orders:
                oid, pid, amt = self.rng.choice(prior_orders)
                half = amt // 2
                lines.append(self._refund_line(setl_id, utr, d, oid, pid, half,
                                               d - timedelta(days=1), partial=True))
                # Remainder lands in the next settlement, keeping the paise exact.
                self._pending_splits.append((oid, pid, amt - half))
                note = f"Order {oid} refunded in two parts across consecutive settlements"

        expected_net = sum(l.net for l in lines)

        # --- bank side ----------------------------------------------------

        value_date = d
        ref_no = utr
        narration = self.rng.choice(NARRATION_TEMPLATES).format(utr=utr)
        credit_amount = expected_net
        extra_rows: list[BankCredit] = []

        if cls is AnomalyClass.FEE_TAX_ROUNDING:
            drift = getattr(self, "_rounding_drift", 1)
            credit_amount = expected_net + drift
            injected = drift

        elif cls is AnomalyClass.MISSING_UTR:
            ref_no = self.rng.choice(["", "NA", "-", "REF NOT AVAILABLE"])
            tmpl = self.rng.choice(GARBLED_NARRATIONS)
            narration = tmpl.format(partial=utr[:5], typo=utr[:4] + "XXXX")
            note = "Bank reference field unusable; identity recoverable only from narration"

        elif cls is AnomalyClass.TIMING_CUT:
            value_date = d + timedelta(days=self.rng.randint(1, 3))
            note = f"Payout settled {d} but credited {value_date}, crossing the period cut-off"

        elif cls is AnomalyClass.DUPLICATE_BANK_CREDIT:
            # The bank credited the same payout twice. This is money the merchant
            # has not earned and the bank will reverse. Auto-clearing it would let
            # the merchant spend funds that are about to vanish -> must escalate.
            extra_rows.append(BankCredit(
                txn_id=self._id("btxn"),
                value_date=(d + timedelta(days=1)).isoformat(),
                narration=self.rng.choice(NARRATION_TEMPLATES).format(utr=utr),
                ref_no=utr,
                debit=0,
                credit=expected_net,
            ))
            injected = expected_net
            note = "Same UTR credited twice by the bank; second credit is unearned"

        elif cls is AnomalyClass.BANK_CHARGE_ADJUSTMENT:
            # The charge is levied as its OWN debit row carrying the same UTR --
            # which is how it appears on a real statement, and which is the only
            # thing distinguishing it from a shortfall of the same size.
            charge = self.rng.choice([500, 1000, 1770, 2360, 5900])  # Rs 5 - Rs 59
            extra_rows.append(BankCredit(
                txn_id=self._id("btxn"),
                value_date=value_date.isoformat(),
                narration=self.rng.choice([
                    "NEFT CHARGES INCL GST",
                    "RTGS OUTWARD CHARGES + GST",
                    "FUND TRANSFER CHARGES",
                ]),
                ref_no=utr,
                debit=charge,
                credit=0,
            ))
            injected = -charge
            note = f"Bank levied a transfer charge of {charge} paise, itemised as a separate debit"

        elif cls is AnomalyClass.TRUE_MISMATCH:
            # Deliberately overlapping the bank-charge magnitude band. If true
            # mismatches were always larger than any plausible charge, a size
            # threshold would separate them and the engine would look far better
            # than it is. Forcing the overlap means the engine has to seek
            # CORROBORATING EVIDENCE -- an itemised charge row -- rather than
            # guessing from magnitude.
            gap = self.rng.randint(2_000, 500_000)   # Rs 20 - Rs 5,000
            if self.rng.random() < 0.5:
                gap = -gap
            credit_amount = expected_net + gap
            injected = gap
            note = "Material unexplained difference between payout and bank credit"

        rows = [BankCredit(
            txn_id=self._id("btxn"),
            value_date=value_date.isoformat(),
            narration=narration,
            ref_no=ref_no,
            debit=0,
            credit=credit_amount,
        )] + extra_rows

        self.lines.extend(lines)
        self.bank.extend(rows)
        self.truth.append(GroundTruth(
            settlement_id=setl_id,
            true_class=cls,
            expected_disposition=expected_disposition(cls),
            injected_delta=injected,
            note=note or "All three sources agree",
        ))


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def _write_csv(path: Path, rows: list, fieldnames: list[str]):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            d = asdict(r)
            for k, v in list(d.items()):
                if hasattr(v, "value"):
                    d[k] = v.value
                elif v is None:
                    d[k] = ""
            w.writerow(d)


def write_dataset(outdir: Path, seed: int = 42, n_settlements: int = 60):
    outdir.mkdir(parents=True, exist_ok=True)
    gen = Generator(seed=seed, n_settlements=n_settlements)
    lines, bank, orders, truth = gen.run()

    _write_csv(outdir / "settlement_recon.csv", lines, [
        "entity_id", "type", "debit", "credit", "amount", "currency", "fee", "tax",
        "settlement_id", "settlement_utr", "created_at", "settled_at",
        "payment_id", "order_id", "method", "description",
    ])
    _write_csv(outdir / "bank_statement.csv", bank, [
        "txn_id", "value_date", "narration", "ref_no", "debit", "credit",
    ])
    _write_csv(outdir / "order_ledger.csv", orders, [
        "order_id", "order_date", "customer_id", "gross_amount", "currency",
        "status", "payment_id",
    ])
    (outdir / "ground_truth.json").write_text(
        json.dumps([t.to_dict() for t in truth], indent=2), encoding="utf-8"
    )
    return lines, bank, orders, truth


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description="Generate the synthetic recon dataset.")
    p.add_argument("--out", default="data", help="output directory")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--settlements", type=int, default=120)
    p.add_argument("--quick", action="store_true",
                   help="small batch (8 settlements) for fast iteration")
    a = p.parse_args(argv)

    n = 8 if a.quick else a.settlements
    lines, bank, orders, truth = write_dataset(Path(a.out), a.seed, n)

    from collections import Counter
    dist = Counter(t.true_class.value for t in truth)
    print(f"wrote {len(lines)} settlement lines / {len(truth)} settlements "
          f"/ {len(bank)} bank rows / {len(orders)} orders -> {a.out}/")
    print("\nground-truth class distribution:")
    for k, v in dist.most_common():
        print(f"  {k:26} {v:3}")


if __name__ == "__main__":
    main()
