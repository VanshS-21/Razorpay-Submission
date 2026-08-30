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
    findings, metrics, _ = main_run
    # If distractors were being consumed, settlements would reconcile against
    # the wrong money and the reason codes would drift.
    assert metrics["classification_accuracy"] > 0.95


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
