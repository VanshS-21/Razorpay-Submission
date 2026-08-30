"""Invariants of the synthetic dataset and its answer key.

These tests guard the foundation. If the generator and the key disagree, every
accuracy number the project reports is meaningless, and the failure is silent --
the engine would simply be graded against the wrong answers. So the key is
tested harder than the engine.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from recon.generate import Generator, allocate, fee_for, DEFAULT_WEIGHTS  # noqa: E402
from recon.models import (  # noqa: E402
    AnomalyClass,
    Disposition,
    EntityType,
    MUST_ESCALATE,
    to_paise,
    rupees,
)


@pytest.fixture(scope="module")
def dataset():
    gen = Generator(seed=42, n_settlements=120)
    lines, bank, orders, truth = gen.run()
    return lines, bank, orders, truth


def _by_settlement(lines, bank, truth):
    l = defaultdict(list)
    for x in lines:
        l[x.settlement_id].append(x)
    t = {x.settlement_id: x for x in truth}
    # Bank rows join on UTR, so map UTR -> settlement via the lines.
    utr_to_setl = {x.settlement_utr: x.settlement_id for x in lines}
    b = defaultdict(list)
    for x in bank:
        sid = utr_to_setl.get(x.ref_no)
        if sid:
            b[sid].append(x)
    return l, b, t


# --------------------------------------------------------------------------
# Money primitives
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,paise", [
    ("1234.56", 123456), ("1234.5", 123450), ("1234", 123400),
    ("0.01", 1), ("-99.99", -9999), ("1,00,000.00", 10000000),
])
def test_to_paise_never_touches_float(text, paise):
    assert to_paise(text) == paise


def test_rupees_pads_paise():
    assert rupees(971) == "Rs 9.71"
    assert rupees(970) == "Rs 9.70"
    assert rupees(97000000) == "Rs 970,000.00"


def test_fee_is_integer_truncated_not_rounded():
    # 2% of Rs 99.99 is 199.98 paise; the ledger must carry 199, not 200.
    fee, tax = fee_for(9999)
    assert fee == 199
    assert tax == 199 * 1800 // 10_000
    assert isinstance(fee, int) and isinstance(tax, int)


# --------------------------------------------------------------------------
# Allocation
# --------------------------------------------------------------------------

def test_allocation_is_exact_and_meets_floor():
    got = allocate(120, DEFAULT_WEIGHTS, floor=5)
    assert len(got) == 120
    counts = Counter(got)
    assert set(counts) == set(DEFAULT_WEIGHTS)
    assert min(counts.values()) >= 5, "every class needs support to be scored"


def test_allocation_degrades_gracefully_when_n_is_small():
    got = allocate(8, DEFAULT_WEIGHTS, floor=5)
    assert len(got) == 8   # floor is unsatisfiable; must not hang or over-allocate


# --------------------------------------------------------------------------
# The answer key
# --------------------------------------------------------------------------

def test_delta_equals_injected_delta(dataset):
    """The core invariant: observed minus expected must equal what we injected.

    This is what makes the key trustworthy. If it holds for all 120 settlements,
    the dataset is internally consistent and the engine can be graded on it.
    """
    lines, bank, orders, truth = dataset
    by_l, by_b, by_t = _by_settlement(lines, bank, truth)

    for sid, gt in by_t.items():
        if gt.true_class is AnomalyClass.MISSING_UTR:
            continue  # bank rows deliberately do not join on ref_no here
        if gt.true_class is AnomalyClass.CONSOLIDATED_PAYOUT:
            continue  # one credit spans two settlements; see the pair test below
        expected = sum(x.net for x in by_l[sid])
        observed = sum(x.credit - x.debit for x in by_b[sid])
        assert observed - expected == gt.injected_delta, (
            f"{sid} ({gt.true_class.value}): delta {observed - expected} "
            f"!= injected {gt.injected_delta}"
        )


def test_clean_settlements_are_actually_clean(dataset):
    """A CLEAN unit must contain nothing but payments and must tie exactly."""
    lines, bank, orders, truth = dataset
    by_l, by_b, by_t = _by_settlement(lines, bank, truth)

    for sid, gt in by_t.items():
        if gt.true_class is not AnomalyClass.CLEAN:
            continue
        types = {x.type for x in by_l[sid]}
        assert types == {EntityType.PAYMENT}, f"{sid} CLEAN but contains {types}"
        assert sum(x.net for x in by_l[sid]) == sum(
            x.credit - x.debit for x in by_b[sid]), f"{sid} CLEAN but does not tie"
        assert len(by_b[sid]) == 1, f"{sid} CLEAN but has multiple bank rows"


def test_rounding_drift_is_sub_rupee(dataset):
    """The tolerance band only makes sense if drift really is tiny."""
    _, _, _, truth = dataset
    drifts = [t.injected_delta for t in truth
              if t.true_class is AnomalyClass.FEE_TAX_ROUNDING]
    assert drifts, "class has no support"
    assert all(0 < abs(d) <= 5 for d in drifts), drifts


def test_true_mismatch_is_materially_larger_than_tolerance(dataset):
    """A true mismatch must never be small enough to hide inside the tolerance.

    If these two classes overlapped in magnitude, no threshold could separate
    them and the false-clear rate would be a property of the data rather than
    of the engine.
    """
    _, _, _, truth = dataset
    gaps = [abs(t.injected_delta) for t in truth
            if t.true_class is AnomalyClass.TRUE_MISMATCH]
    assert gaps
    assert min(gaps) >= 10_000, "true mismatches must be >= Rs 100"


def test_duplicate_credits_have_two_bank_rows(dataset):
    lines, bank, orders, truth = dataset
    by_l, by_b, by_t = _by_settlement(lines, bank, truth)
    for sid, gt in by_t.items():
        if gt.true_class is AnomalyClass.DUPLICATE_BANK_CREDIT:
            assert len(by_b[sid]) == 2, f"{sid} should have a duplicate credit"


def test_missing_utr_reference_is_unusable(dataset):
    """The whole point of the class: ref_no cannot be joined on."""
    lines, bank, orders, truth = dataset
    by_t = {x.settlement_id: x for x in truth}
    utrs = {x.settlement_utr for x in lines}
    good_refs = {b.ref_no for b in bank if b.ref_no in utrs}
    missing = [t for t in truth if t.true_class is AnomalyClass.MISSING_UTR]
    assert missing
    # None of the missing-UTR settlements should have a joinable bank row.
    for t in missing:
        setl_utrs = {l.settlement_utr for l in lines
                     if l.settlement_id == t.settlement_id}
        assert not (setl_utrs & good_refs), f"{t.settlement_id} is still joinable"


def test_consolidated_payouts_come_in_pairs_that_sum_exactly(dataset):
    """One bank credit must equal the sum of exactly two settlement nets.

    If this holds, the subset-sum solver has a real target to find and is not
    just decoration on top of a lookup.
    """
    lines, bank, orders, truth = dataset
    by_l, by_b, by_t = _by_settlement(lines, bank, truth)

    consolidated = [t for t in truth
                    if t.true_class is AnomalyClass.CONSOLIDATED_PAYOUT]
    assert consolidated and len(consolidated) % 2 == 0, "must be paired"

    partner = {}
    for t in consolidated:
        partner[t.settlement_id] = t.note.rsplit(" ", 1)[-1]

    checked = 0
    for sid, other in partner.items():
        credit_rows = by_b.get(sid, [])
        if not credit_rows:
            continue                      # this is the un-quoted half of the pair
        credited = sum(r.credit - r.debit for r in credit_rows)
        combined = sum(x.net for x in by_l[sid]) + sum(x.net for x in by_l[other])
        assert credited == combined, f"{sid}+{other}: {credited} != {combined}"
        checked += 1
    assert checked == len(consolidated) // 2


def test_consolidated_partner_has_no_bank_row_of_its_own(dataset):
    """The second settlement looks unpaid. That is the whole difficulty."""
    lines, bank, orders, truth = dataset
    by_l, by_b, by_t = _by_settlement(lines, bank, truth)
    unpaid = [t.settlement_id for t in truth
              if t.true_class is AnomalyClass.CONSOLIDATED_PAYOUT
              and not by_b.get(t.settlement_id)]
    assert len(unpaid) == sum(
        1 for t in truth if t.true_class is AnomalyClass.CONSOLIDATED_PAYOUT) // 2


def test_expected_disposition_matches_taxonomy(dataset):
    _, _, _, truth = dataset
    for t in truth:
        want = Disposition.EXCEPTION if t.true_class in MUST_ESCALATE else Disposition.RECONCILED
        assert t.expected_disposition == want


def test_escalation_classes_have_real_support(dataset):
    """The two must-escalate classes drive the headline safety metric."""
    _, _, _, truth = dataset
    counts = Counter(t.true_class for t in truth)
    for cls in MUST_ESCALATE:
        assert counts[cls] >= 5, f"{cls.value} has only {counts[cls]} instances"


def test_generation_is_deterministic():
    a = Generator(seed=7, n_settlements=30).run()
    b = Generator(seed=7, n_settlements=30).run()
    assert [x.entity_id for x in a[0]] == [x.entity_id for x in b[0]]
    assert [t.injected_delta for t in a[3]] == [t.injected_delta for t in b[3]]


def test_different_seeds_produce_different_data():
    a = Generator(seed=1, n_settlements=30).run()
    b = Generator(seed=2, n_settlements=30).run()
    assert [x.entity_id for x in a[0]] != [x.entity_id for x in b[0]]


def test_orders_referenced_by_lines_exist(dataset):
    """Three-way means the books side must actually join."""
    lines, bank, orders, truth = dataset
    known = {o.order_id for o in orders}
    for l in lines:
        if l.order_id:
            assert l.order_id in known, f"{l.entity_id} references unknown {l.order_id}"


def test_refunds_are_marked_in_the_order_ledger(dataset):
    lines, bank, orders, truth = dataset
    by_order = {o.order_id: o for o in orders}
    for l in lines:
        if l.type is EntityType.REFUND and l.order_id:
            assert by_order[l.order_id].status in {"refunded", "partially_refunded"}
