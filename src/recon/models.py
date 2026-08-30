"""Core entities for three-way settlement reconciliation.

MONEY IS ALWAYS INTEGER PAISE. Never float, never Decimal-in-CSV.
Every amount field in this module is an ``int`` count of paise (1 INR = 100 paise).
Floats are banned because 0.1 + 0.2 != 0.3, and a reconciliation engine that
cannot decide equality is not a reconciliation engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------

def rupees(paise: int) -> str:
    """Render integer paise as a human-readable INR string."""
    sign = "-" if paise < 0 else ""
    p = abs(int(paise))
    return f"{sign}Rs {p // 100:,}.{p % 100:02d}"


def to_paise(rupee_str: str) -> int:
    """Parse a decimal rupee string into integer paise without touching float.

    '1234.56' -> 123456 ;  '1234.5' -> 123450 ;  '1234' -> 123400
    """
    s = str(rupee_str).strip().replace(",", "").replace("Rs", "").strip()
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    if "." in s:
        whole, frac = s.split(".", 1)
        frac = (frac + "00")[:2]
    else:
        whole, frac = s, "00"
    val = int(whole or "0") * 100 + int(frac or "0")
    return -val if neg else val


# --------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------

class AnomalyClass(str, Enum):
    """The ten situations the generator injects and the engine must recognise.

    CLEAN is the majority case. The rest are the real-world messiness that
    makes settlement reconciliation a manual job at most merchants.
    """

    CLEAN = "clean"
    FEE_TAX_ROUNDING = "fee_tax_rounding"
    REFUND_NETTED_LATER = "refund_netted_later"
    CHARGEBACK_DEDUCTION = "chargeback_deduction"
    MISSING_UTR = "missing_utr"
    TIMING_CUT = "timing_cut"
    DUPLICATE_BANK_CREDIT = "duplicate_bank_credit"
    SPLIT_REFUND = "split_refund"
    BANK_CHARGE_ADJUSTMENT = "bank_charge_adjustment"
    TRUE_MISMATCH = "true_mismatch"


class Disposition(str, Enum):
    """What the engine decided to do with a reconciliation unit."""

    RECONCILED = "reconciled"   # explained end to end; no human needed
    EXCEPTION = "exception"     # cannot be explained safely; needs a human


#: Classes that MUST end up as an exception. Auto-clearing either of these is a
#: false-clear: the expensive, trust-destroying error in a finance system.
#:  - TRUE_MISMATCH: real money discrepancy.
#:  - DUPLICATE_BANK_CREDIT: money in the account that was not earned. Silently
#:    clearing it means the merchant spends funds the bank will claw back.
MUST_ESCALATE: frozenset = frozenset({
    AnomalyClass.TRUE_MISMATCH,
    AnomalyClass.DUPLICATE_BANK_CREDIT,
})


def expected_disposition(cls: AnomalyClass) -> Disposition:
    return Disposition.EXCEPTION if cls in MUST_ESCALATE else Disposition.RECONCILED


class EntityType(str, Enum):
    """`type` column of the Razorpay settlement recon report."""

    PAYMENT = "payment"
    REFUND = "refund"
    DISPUTE = "dispute"
    ADJUSTMENT = "adjustment"


# --------------------------------------------------------------------------
# Source records (one dataclass per input file)
# --------------------------------------------------------------------------

@dataclass
class SettlementLine:
    """One row of the PSP settlement recon report.

    Field names mirror Razorpay's real recon report so the artefact is
    recognisable to anyone who has opened one:
    https://razorpay.com/docs/api/settlements/fetch-recon/
    """

    entity_id: str              # pay_xxx / rfnd_xxx / disp_xxx / adj_xxx
    type: EntityType
    debit: int                  # paise taken out of the merchant balance
    credit: int                 # paise put into the merchant balance
    amount: int                 # gross transaction value
    currency: str
    fee: int                    # Razorpay fee
    tax: int                    # GST on the fee
    settlement_id: str          # setl_xxx
    settlement_utr: str         # bank UTR for the payout
    created_at: str             # ISO date
    settled_at: str             # ISO date
    payment_id: Optional[str] = None
    order_id: Optional[str] = None
    method: Optional[str] = None
    description: str = ""

    @property
    def net(self) -> int:
        """Contribution of this line to the settlement payout, in paise."""
        return self.credit - self.debit


@dataclass
class BankCredit:
    """One row of the merchant's bank statement.

    The bank does not see payments. It sees one net lump sum per payout, and a
    narration string somebody wrote a regex for once and never revisited.
    """

    txn_id: str
    value_date: str
    narration: str
    ref_no: str                 # UTR -- may be blank or garbled (MISSING_UTR)
    debit: int
    credit: int


@dataclass
class OrderRecord:
    """One row of the merchant's own order ledger / ERP export."""

    order_id: str
    order_date: str
    customer_id: str
    gross_amount: int
    currency: str
    status: str                 # paid | refunded | partially_refunded
    payment_id: Optional[str] = None


# --------------------------------------------------------------------------
# Reconciliation output
# --------------------------------------------------------------------------

@dataclass
class ReconUnit:
    """The unit of reconciliation: one settlement batch and its bank credit.

    A settlement groups many recon-report lines into a single payout. The bank
    credits the NET of those lines under one UTR. Deciding whether that net is
    correct is the whole problem.
    """

    settlement_id: str
    utr: str
    lines: list = field(default_factory=list)
    bank_credits: list = field(default_factory=list)

    @property
    def expected_net(self) -> int:
        """What the bank should have credited, from the PSP side alone."""
        return sum(l.net for l in self.lines)

    @property
    def observed_net(self) -> int:
        """What the bank actually credited under this UTR."""
        return sum(b.credit - b.debit for b in self.bank_credits)

    @property
    def delta(self) -> int:
        """Unexplained difference in paise. Zero is the goal."""
        return self.observed_net - self.expected_net


@dataclass
class Finding:
    """The engine's verdict on one ReconUnit -- the row that gets graded."""

    settlement_id: str
    utr: str
    disposition: Disposition
    reason_code: AnomalyClass
    delta: int
    explanation: str
    action_required: str = ""            # what a human must do; empty if reconciled
    resolved_by: str = "deterministic"   # deterministic | llm | llm+guard
    confidence: float = 1.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["disposition"] = self.disposition.value
        d["reason_code"] = self.reason_code.value
        d["delta_inr"] = rupees(self.delta)
        return d


@dataclass
class GroundTruth:
    """The answer key emitted by the generator alongside the data.

    Without this, 'match rate' is an unfalsifiable claim. With it, every number
    in the report is checkable by anyone who clones the repo.
    """

    settlement_id: str
    true_class: AnomalyClass
    expected_disposition: Disposition
    injected_delta: int
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["true_class"] = self.true_class.value
        d["expected_disposition"] = self.expected_disposition.value
        return d
