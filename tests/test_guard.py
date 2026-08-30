"""The guard: everything a language model produces must survive arithmetic.

These run without an API key, because they test the verification layer rather
than the model. That is the point of putting the safety logic in a pure function
-- the thing that protects the books is testable offline and deterministically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recon.agent.guard import (      # noqa: E402
    GuardStats,
    allowed_figures_for,
    verify_match,
    verify_narration,
)
from recon.models import (           # noqa: E402
    AnomalyClass,
    BankCredit,
    Disposition,
    EntityType,
    Finding,
    ReconUnit,
    SettlementLine,
)


def _line(amount=100000, credit=None, fee=2000, tax=360, **kw):
    return SettlementLine(
        entity_id="pay_x", type=EntityType.PAYMENT, debit=0,
        credit=amount - fee - tax if credit is None else credit,
        amount=amount, currency="INR", fee=fee, tax=tax,
        settlement_id=kw.get("sid", "setl_1"), settlement_utr=kw.get("utr", "UTR1"),
        created_at="2026-06-01", settled_at=kw.get("settled", "2026-06-03"),
        order_id="order_1", payment_id="pay_x")


def _unit(**kw):
    u = ReconUnit(settlement_id=kw.get("sid", "setl_1"),
                  utr=kw.get("utr", "UTR1"), lines=[_line(**kw)])
    u.bank_credits = kw.get("bank", [])
    return u


def _finding(delta=0):
    return Finding(settlement_id="setl_1", utr="UTR1",
                   disposition=Disposition.EXCEPTION,
                   reason_code=AnomalyClass.TRUE_MISMATCH,
                   delta=delta, explanation="x")


# --------------------------------------------------------------------------
# Narration: invented figures must be caught
# --------------------------------------------------------------------------

def test_narration_with_only_real_figures_is_accepted():
    s = GuardStats()
    assert verify_narration("Short by Rs 500.00 against the payout.",
                            {50000}, s) is True
    assert s.rejected == 0


def test_narration_with_an_invented_figure_is_rejected():
    """The whole reason the guard exists.

    A plausible wrong number in a finance note is worse than no note: it is a
    figure a human will quote to their bank.
    """
    s = GuardStats()
    assert verify_narration("Short by Rs 512.00 against the payout.",
                            {50000}, s) is False
    assert s.rejected == 1
    assert any("invented_figure" in k for k in s.reasons)


def test_narration_figure_formats_are_normalised():
    """Rs 1,00,000.00 and Rs 100000 are the same number."""
    s = GuardStats()
    assert verify_narration("Payout of Rs 1,00,000.00 was credited.",
                            {10000000}, s) is True
    assert verify_narration("Payout of Rs 100000 was credited.",
                            {10000000}, s) is True


def test_narration_with_no_figures_is_accepted():
    s = GuardStats()
    assert verify_narration("The reference field was unusable.", set(), s) is True


def test_allowed_figures_include_components_not_just_totals():
    u = _unit()
    f = _finding(delta=-500)
    allowed = allowed_figures_for(u, f)
    assert 100000 in allowed        # gross amount
    assert 2000 in allowed          # fee
    assert 360 in allowed           # tax
    assert 500 in allowed           # the delta itself


def test_zero_is_a_citable_figure():
    """Rs 0.00 must be allowed.

    In a ledger mismatch the difference IS zero -- the settlement ties to the
    paise and only the books disagree. Saying so is the most important sentence
    in the note, and an earlier version of the guard rejected it.
    """
    u = _unit()
    f = _finding(delta=0)
    assert 0 in allowed_figures_for(u, f)
    s = GuardStats()
    assert verify_narration(
        "The settlement ties exactly: the difference is Rs 0.00.",
        allowed_figures_for(u, f), s) is True
    assert s.rejected == 0


# --------------------------------------------------------------------------
# Matching: a proposal is only a proposal
# --------------------------------------------------------------------------

def _row(credit, date="2026-06-03"):
    return BankCredit(txn_id="b1", value_date=date, narration="NEFT",
                      ref_no="", debit=0, credit=credit)


def test_match_accepted_when_amount_and_date_tie():
    u = _unit()
    s = GuardStats()
    assert verify_match({"settlement_id": "setl_1"}, {"setl_1": u},
                        _row(u.expected_net), s) is True
    assert s.rejected == 0


def test_match_rejected_when_amount_is_off_by_one_paisa():
    """Exact means exact. A model confident to the rupee is not good enough."""
    u = _unit()
    s = GuardStats()
    assert verify_match({"settlement_id": "setl_1"}, {"setl_1": u},
                        _row(u.expected_net + 1), s) is False
    assert s.reasons.get("amount_mismatch") == 1


def test_match_rejected_outside_the_date_window():
    u = _unit()
    s = GuardStats()
    assert verify_match({"settlement_id": "setl_1"}, {"setl_1": u},
                        _row(u.expected_net, date="2026-07-20"), s) is False
    assert s.reasons.get("outside_date_window") == 1


def test_match_rejected_for_hallucinated_settlement_id():
    u = _unit()
    s = GuardStats()
    assert verify_match({"settlement_id": "setl_nope"}, {"setl_1": u},
                        _row(u.expected_net), s) is False
    assert s.reasons.get("unknown_settlement_id") == 1


def test_match_rejected_when_settlement_already_has_a_credit():
    """A model must not be able to reassign money that is already accounted for."""
    u = _unit(bank=[_row(1)])
    s = GuardStats()
    assert verify_match({"settlement_id": "setl_1"}, {"setl_1": u},
                        _row(u.expected_net), s) is False
    assert s.reasons.get("settlement_already_matched") == 1


@pytest.mark.parametrize("proposal", [None, {}, {"settlement_id": None},
                                      {"settlement_id": ""}])
def test_match_rejected_when_nothing_is_proposed(proposal):
    u = _unit()
    s = GuardStats()
    assert verify_match(proposal, {"setl_1": u}, _row(u.expected_net), s) is False


def test_guard_stats_track_rejection_rate():
    s = GuardStats()
    u = _unit()
    verify_match({"settlement_id": "setl_1"}, {"setl_1": u}, _row(u.expected_net), s)
    verify_match({"settlement_id": "bad"}, {"setl_1": u}, _row(u.expected_net), s)
    assert s.checked == 2
    assert s.rejected == 1
    assert s.rejection_rate == 0.5

# --------------------------------------------------------------------------
# What counts as a rupee figure
#
# The pattern used to be `Rs\s*([\d,]+...)` under a docstring claiming it
# extracted "every rupee figure". It recognised exactly one spelling, and the
# tests used only that spelling -- so they confirmed the implementation and
# told us nothing about the property. Nine of the fifteen forms below walked
# straight past it, including "Rs." which is the commonest of the lot.
# --------------------------------------------------------------------------

CAUGHT = [
    "the bank is short by Rs 5,000.00",
    "the bank is short by Rs. 5,000.00",
    "the bank is short by RS 5,000.00",
    "the bank is short by rs 5,000.00",
    "the bank is short by INR 5,000.00",
    "the bank is short by ₹5,000.00",
    "the bank is short by &#8377;5,000.00",
    "the bank is short by 5,000.00 rupees",
    "the bank is short by Rs&nbsp;5,000.00",
    "the bank is short by Rs 5000",
    "the bank is short by 5000 INR",
]


@pytest.mark.parametrize("text", CAUGHT)
def test_an_invented_figure_is_caught_however_it_is_spelled(text):
    stats = GuardStats()
    # 123456 paise is the only figure the engine computed; 5000.00 is invented.
    assert verify_narration(text, {123456}, stats) is False
    assert stats.rejected == 1


@pytest.mark.parametrize("text", CAUGHT)
def test_the_same_spellings_pass_when_the_figure_is_real(text):
    """A guard that rejects everything is safe and useless.

    Every spelling above must also be ACCEPTED when the number is one the
    engine actually derived, or the broadened pattern would just be a new way
    to throw away good notes.
    """
    stats = GuardStats()
    assert verify_narration(text, {500000}, stats) is True
    assert stats.rejected == 0


@pytest.mark.parametrize("text", [
    "a shortfall of Rs ,",          # interrupted thousands separator
    "a shortfall of Rs .",
    "a shortfall of Rs",
    "",
    "order_12345 and payment_99 need review",   # digits, but not money
])
def test_malformed_output_is_survived_not_crashed(text):
    """The guard must never be the thing that takes down the run.

    "Rs ," reached int("") and raised, from inside the one function whose whole
    job is to survive whatever a model emits -- so the component protecting the
    report was also the one component that could destroy it.
    """
    stats = GuardStats()
    assert verify_narration(text, {123456}, stats) is True


def test_an_order_id_is_not_read_as_money():
    """Bare digits are not figures; only digits wearing a currency are."""
    stats = GuardStats()
    assert verify_narration(
        "order_78910 was booked twice under reference 55512345",
        {123456}, stats) is True
