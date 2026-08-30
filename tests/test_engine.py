"""Engine behaviour, with the safety properties pinned hardest.

The tests that matter most are the ones asserting the engine REFUSES to clear
something. A reconciliation engine that is too eager costs money; one that is
too cautious costs review time. The asymmetry is the whole design, so it is the
thing most worth a regression guard.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recon.adversarial import write_holdout            # noqa: E402
from recon.engine.matcher import subset_summing_to     # noqa: E402
from recon.engine.pipeline import run                  # noqa: E402
from recon.generate import write_dataset               # noqa: E402
from recon.models import AnomalyClass, Disposition     # noqa: E402
from recon.report import load_truth, score             # noqa: E402


@pytest.fixture(scope="module")
def main_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("main")
    write_dataset(d, seed=42, n_settlements=120)
    findings, m, units, timing, _a = run(d)
    return findings, score(findings, load_truth(d)), d


@pytest.fixture(scope="module")
def holdout_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("holdout")
    write_holdout(d, seed=1337)
    findings, m, units, timing, _a = run(d)
    return findings, score(findings, load_truth(d)), d


# --------------------------------------------------------------------------
# Safety: the properties that must never regress
# --------------------------------------------------------------------------

def test_no_false_clears_on_main_set(main_run):
    _, metrics, _ = main_run
    assert metrics["false_clear_count"] == 0, metrics["_false_clear"]


def test_no_false_clears_on_adversarial_holdout(holdout_run):
    """The holdout caught 4 false clears on its first run. Never again."""
    _, metrics, _ = holdout_run
    assert metrics["false_clear_count"] == 0, [
        (f.settlement_id, f.reason_code.value, gt["true_class"])
        for f, gt in metrics["_false_clear"]]


@pytest.mark.parametrize("seed", [1337, 2024, 90210, 55555])
def test_holdout_safety_generalises_across_seeds(tmp_path, seed):
    write_holdout(tmp_path, seed=seed)
    findings, _, _, _, _ = run(tmp_path)
    metrics = score(findings, load_truth(tmp_path))
    assert metrics["false_clear_count"] == 0


def test_phantom_refund_is_never_cleared(holdout_run):
    """A refund the order ledger does not corroborate must escalate.

    This is the bug the holdout found: the settlement ties to the bank exactly,
    because a refund reduces both sides equally. Only the books disagree.
    """
    findings, _, d = holdout_run
    truth = load_truth(d)
    phantom = [f for f in findings
               if "phantom_refund" in truth[f.settlement_id]["note"]]
    assert phantom, "holdout should contain phantom refunds"
    for f in phantom:
        assert f.disposition is Disposition.EXCEPTION, (
            f"{f.settlement_id} cleared a refund the ledger never authorised")
        assert f.reason_code is AnomalyClass.LEDGER_MISMATCH


def test_duplicate_credit_always_escalates(main_run):
    findings, _, d = main_run
    truth = load_truth(d)
    dups = [f for f in findings
            if truth[f.settlement_id]["true_class"] == "duplicate_bank_credit"]
    assert dups
    assert all(f.disposition is Disposition.EXCEPTION for f in dups)


def test_unexplained_shortfall_is_not_written_off_as_a_bank_charge(main_run):
    """Magnitude is not evidence. Without an itemised charge row, a small
    shortfall must escalate even though it is charge-sized."""
    findings, _, d = main_run
    truth = load_truth(d)
    for f in findings:
        if truth[f.settlement_id]["true_class"] != "true_mismatch":
            continue
        assert f.disposition is Disposition.EXCEPTION, f.settlement_id


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def test_subset_sum_is_exact_not_approximate():
    cands = [("a", 100), ("b", 250), ("c", 375)]
    assert sorted(subset_summing_to(350, cands)) == ["a", "b"]
    assert subset_summing_to(351, cands) is None, "must not accept near misses"
    assert subset_summing_to(0, cands) is None
    assert subset_summing_to(100, []) is None


def test_subset_sum_respects_max_size():
    cands = [("a", 1), ("b", 2), ("c", 3), ("d", 4)]
    assert subset_summing_to(10, cands, max_size=3) is None
    assert subset_summing_to(10, cands, max_size=4) is not None


def test_consolidated_payouts_resolve(main_run):
    findings, _, d = main_run
    truth = load_truth(d)
    cons = [f for f in findings
            if truth[f.settlement_id]["true_class"] == "consolidated_payout"]
    assert cons
    assert all(f.disposition is Disposition.RECONCILED for f in cons)
    assert all(f.reason_code is AnomalyClass.CONSOLIDATED_PAYOUT for f in cons)


def test_distractor_bank_rows_are_left_alone(main_run):
    """A statement carries payroll and other gateways. Consuming one of those
    to force a match would be worse than leaving a settlement unmatched."""
    findings, metrics, d = main_run
    # This used to assert only `classification_accuracy > 0.95`, which is a
    # statement about the whole engine and fires for reasons that have nothing
    # to do with distractors -- it caught two unrelated arithmetic mutations
    # during an audit and would pass if every distractor were consumed. Look at
    # the rows themselves.
    _, m, _, _, _ = run(d)
    narrations = {r.narration for r in m.orphan_bank}
    assert any("SALARY" in n or "VENDOR" in n or "CASHFREE" in n or "PHONEPE" in n
               for n in narrations), (
        "no unrelated account traffic was left alone; the matcher is consuming "
        "rows it has no business claiming")
    for sid, rows in m.assigned.items():
        for r in rows:
            assert "SALARY DISBURSEMENT" not in r.narration, (
                f"{sid} was matched against a payroll run: {r.narration}")


# --------------------------------------------------------------------------
# Threshold and rule pinning
#
# Every test below was written after an audit mutated a constant or a rule and
# the whole suite still passed. A threshold nothing asserts is a threshold
# nobody chose -- and the two most dangerous survivors were widening the
# rounding tolerance a hundredfold and turning "no bank credit found for this
# payout" into RECONCILED.
# --------------------------------------------------------------------------

def test_a_payout_with_no_bank_credit_always_escalates(tmp_path):
    """The single most expensive verdict in the system to get wrong.

    Mutating this rule to RECONCILED passed all 72 tests, and there was no
    fixture anywhere in the suite where a payout simply never arrived -- the
    generator always credits something. It means the merchant is told money
    landed when nothing did.
    """
    import csv
    rows = [dict(entity_id="trf_1", type="payment", debit=0, credit=97_640,
                 amount=100_000, currency="INR", fee=2_000, tax=360,
                 settlement_id="setl_GONE", settlement_utr="UTRGONE",
                 created_at="2026-06-01", settled_at="2026-06-02",
                 payment_id="pay_1", order_id="order_1", method="upi",
                 description="Payment")]
    with (tmp_path / "settlement_recon.csv").open("w", newline="",
                                                  encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for name, hdr in (("bank_statement.csv",
                       ["txn_id", "value_date", "narration", "ref_no",
                        "debit", "credit"]),):
        with (tmp_path / name).open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(hdr)
    with (tmp_path / "order_ledger.csv").open("w", newline="",
                                              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["order_id", "order_date",
                                           "customer_id", "gross_amount",
                                           "currency", "status", "payment_id"])
        w.writeheader()
        w.writerow(dict(order_id="order_1", order_date="2026-06-01",
                        customer_id="c1", gross_amount=100_000, currency="INR",
                        status="paid", payment_id="pay_1"))

    findings, m, units, _, _ = run(tmp_path)
    assert not m.assigned["setl_GONE"], "fixture credited the payout after all"
    f = findings[0]
    assert f.disposition is Disposition.EXCEPTION, (
        "a payout with no bank credit anywhere was cleared")
    assert f.reason_code is AnomalyClass.TRUE_MISMATCH
    assert f.delta == -97_640


@pytest.mark.parametrize("delta,absorbed", [
    (0, False), (5, True), (-5, True), (6, False), (-6, False), (500, False),
])
def test_the_rounding_tolerance_is_exactly_five_paise(delta, absorbed):
    """Widening this to Rs 5.00 passed every test in the suite.

    It is the constant behind the documented "sub-rupee blindness" limitation,
    so its exact value is a published claim and belongs under a test.
    """
    from recon.engine.arithmetic import ROUNDING_TOLERANCE_PAISE, within_rounding
    assert ROUNDING_TOLERANCE_PAISE == 5
    # Zero is not "within tolerance", it is exact, and an earlier rule owns it.
    assert within_rounding(delta) is absorbed


def test_a_surplus_is_never_written_off_as_a_transfer_charge():
    """A charge is money LEAVING. Extra money arriving is never explained by one.

    Dropping the sign check passed every test, which would let an unexplained
    over-credit be absorbed as though the bank had charged for it.
    """
    from recon.engine.arithmetic import looks_like_bank_charge
    assert looks_like_bank_charge(-5_900) is True
    assert looks_like_bank_charge(5_900) is False
    assert looks_like_bank_charge(-10_001) is False


def test_expected_credit_subtracts_both_fee_and_tax():
    """Dropping GST from the identity passed every test in the suite."""
    from recon.engine.arithmetic import expected_credit
    from recon.models import EntityType, SettlementLine
    l = SettlementLine(
        entity_id="trf_1", type=EntityType.PAYMENT, debit=0, credit=0,
        amount=100_000, currency="INR", fee=2_000, tax=360,
        settlement_id="setl_1", settlement_utr="UTR1",
        created_at="2026-06-01", settled_at="2026-06-02")
    assert expected_credit(l) == 100_000 - 2_000 - 360


def test_the_charge_vocabulary_is_not_empty():
    """Emptying CHARGE_WORDS passed every test, silently disabling the evidence
    rule that is the only thing separating a real shortfall from a bank fee."""
    from recon.engine.classify import CHARGE_WORDS, _is_charge_narration
    assert CHARGE_WORDS
    assert _is_charge_narration("NEFT CHARGES INCL GST")
    assert not _is_charge_narration("NEFT CR-RAZORPAY-UTR123")


def test_bank_charges_are_recalled_completely(main_run):
    """Recall on this class is what dies when the charge vocabulary is broken."""
    _, metrics, _ = main_run
    row = next(r for r in metrics["per_class"]
               if r["class"] == "bank_charge_adjustment")
    assert row["support"] >= 5
    assert row["recall"] == 1.0


def test_the_date_window_is_three_days():
    """Narrowing this to 0 passed every test; it is a published constant."""
    from recon.engine.arithmetic import DATE_WINDOW_DAYS
    assert DATE_WINDOW_DAYS == 3


# --------------------------------------------------------------------------
# Bank-side coverage
# --------------------------------------------------------------------------

def test_unmatched_bank_rows_are_reported_not_discarded(main_run):
    """A three-way reconciler must account for the statement, not just payouts.

    These rows were computed and thrown away: nothing in the console output,
    run.json or the HTML said how much money crossed the account unexplained.
    """
    _, _, d = main_run
    findings, m, _, timing, _ = run(d)
    metrics = score(findings, load_truth(d))
    from recon.report import render_console
    text = render_console(metrics, findings, timing, m)
    assert "BANK-SIDE COVERAGE" in text
    assert str(len(m.orphan_bank)) in text


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------

def test_every_exception_carries_an_action(main_run, holdout_run):
    """An exception list without instructions is just a list of problems."""
    for findings, _, _ in (main_run, holdout_run):
        for f in findings:
            if f.disposition is Disposition.EXCEPTION:
                assert f.action_required.strip(), f.settlement_id
                assert f.explanation.strip(), f.settlement_id


def test_findings_cover_every_settlement(main_run):
    findings, metrics, d = main_run
    truth = load_truth(d)
    assert len(findings) == len(truth)
    assert {f.settlement_id for f in findings} == set(truth)


def test_a_line_that_does_not_add_up_escalates(tmp_path):
    """credit must equal amount - fee - tax, on every row.

    `line_drift` computed this and had no callers anywhere in the engine, so a
    settlement built from self-contradictory rows reconciled normally provided
    the contradictions cancelled in the total -- the expected net itself was
    then wrong, and everything measured against it inherited the error.
    """
    import csv
    rows = [dict(entity_id="trf_1", type="payment", debit=0,
                 credit=97_640 + 5_000,          # 5,000 paise more than it should be
                 amount=100_000, currency="INR", fee=2_000, tax=360,
                 settlement_id="setl_BAD", settlement_utr="UTRBAD",
                 created_at="2026-06-01", settled_at="2026-06-02",
                 payment_id="pay_1", order_id="order_1", method="upi",
                 description="Payment")]
    with (tmp_path / "settlement_recon.csv").open("w", newline="",
                                                  encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with (tmp_path / "bank_statement.csv").open("w", newline="",
                                                encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["txn_id", "value_date", "narration",
                                           "ref_no", "debit", "credit"])
        w.writeheader()
        w.writerow(dict(txn_id="btxn_1", value_date="2026-06-02",
                        narration="NEFT CR-RAZORPAY-UTRBAD", ref_no="UTRBAD",
                        debit=0, credit=97_640 + 5_000))
    with (tmp_path / "order_ledger.csv").open("w", newline="",
                                              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["order_id", "order_date",
                                           "customer_id", "gross_amount",
                                           "currency", "status", "payment_id"])
        w.writeheader()
        w.writerow(dict(order_id="order_1", order_date="2026-06-01",
                        customer_id="c1", gross_amount=100_000, currency="INR",
                        status="paid", payment_id="pay_1"))

    findings, _, _, _, _ = run(tmp_path)
    f = findings[0]
    # The bank agrees with the (wrong) stated credit, so every totals-based
    # check ties perfectly. Only the per-line identity catches it.
    assert f.disposition is Disposition.EXCEPTION
    assert f.reason_code is AnomalyClass.LEDGER_MISMATCH
    assert "do not add up internally" in f.explanation
