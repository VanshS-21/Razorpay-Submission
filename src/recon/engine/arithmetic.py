"""Money arithmetic and the tolerance bands.

Every threshold in this file is a decision about when to stop looking at a
discrepancy, which is the same thing as deciding what the system is willing to
miss. They are therefore stated with their justification and their failure mode,
not tuned until the metrics looked good.

No language model touches anything in this module. Arithmetic on money is the
one thing in the pipeline that must be exactly reproducible, and a model that is
right 99.9% of the time is wrong about a rupee once every thousand rows.
"""

from __future__ import annotations

from .. import models
from ..models import EntityType, SettlementLine

# --------------------------------------------------------------------------
# Tolerances
# --------------------------------------------------------------------------

#: Sub-rupee drift absorbed without raising an exception.
#:
#: Justification: the PSP computes fee and 18% GST per line and truncates each to
#: paise; the bank moves one aggregate. Truncating N lines can differ from
#: truncating their sum by up to N-1 paise. At ~10 lines per settlement, a few
#: paise is arithmetic, not error.
#:
#: Failure mode: a genuine discrepancy smaller than this is invisible. That is
#: accepted deliberately -- chasing 3 paise costs more than 3 paise -- but it
#: means this system cannot detect sub-rupee skimming, and that limitation is
#: stated in the README rather than left for someone to discover.
ROUNDING_TOLERANCE_PAISE = 5

#: Upper bound on what will be written off as a bank transfer charge.
#:
#: Justification: NEFT/RTGS/IMPS charges in India are tens of rupees plus GST,
#: not hundreds. Rs 100 sits above the real ceiling with margin.
#:
#: Failure mode: this is a THRESHOLD, not a fee schedule. A real deployment
#: should match the exact debit against the bank's published charge grid, so
#: that an unexpected Rs 40 debit is queried rather than absorbed.
#:
#: This comment used to claim the injected charges and true mismatches never
#: overlap, and that the README said so -- the README said the opposite, and a
#: test asserted the separation the README claimed to have removed. On seed 42
#: they do not overlap (Rs 59 vs Rs 224); on seeds 13, 28, 31, 33 and 100 they
#: do. The rule does not rely on the gap either way: a shortfall is written off
#: only when the statement ITEMISES a charge for it, never on size alone, and
#: test_engine_holds_where_the_magnitude_bands_overlap runs the overlapping
#: seeds to prove it.
BANK_CHARGE_MAX_PAISE = 10_000

#: How far after the settlement date a bank credit may land and still be
#: considered the same payout. NEFT batches settle same-day or next working day;
#: a weekend plus a bank holiday is three days.
DATE_WINDOW_DAYS = 3


# --------------------------------------------------------------------------
# Identities
# --------------------------------------------------------------------------

def expected_credit(line: SettlementLine) -> int:
    """What a payment line's credit should be, from its own components."""
    return line.amount - line.fee - line.tax


def line_drift(line: SettlementLine) -> int:
    """Per-line inconsistency between the stated credit and its components.

    Non-zero means the PSP's own row does not add up. Only meaningful for
    payments; refunds and disputes use debit instead.
    """
    if line.type is not EntityType.PAYMENT:
        return 0
    return line.credit - expected_credit(line)


def within_rounding(delta: int) -> bool:
    return 0 < abs(delta) <= ROUNDING_TOLERANCE_PAISE


def looks_like_bank_charge(delta: int) -> bool:
    """A small DEBIT against the payout. Direction matters: a bank charge can
    only ever reduce the credit. An unexplained small *surplus* is not a charge
    and must not be written off as one."""
    return delta < 0 and ROUNDING_TOLERANCE_PAISE < abs(delta) <= BANK_CHARGE_MAX_PAISE
