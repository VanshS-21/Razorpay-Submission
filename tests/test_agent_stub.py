"""End-to-end agent-path tests driven by a scripted client.

No API key, no network. These do not measure model quality -- nothing here says
anything about how good a real model would be. They assert that MY code behaves
correctly when a model misbehaves, which is the part I can actually be held
responsible for.

The property under test, in one line: **a misbehaving model must not be able to
change what the books say.**
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recon.agent.fake import (  # noqa: E402
    IMPOSSIBLE_FIGURE,
    SCENARIOS,
    ScriptedClient,
)
from recon.engine.pipeline import run                            # noqa: E402
from recon.generate import write_dataset                         # noqa: E402
from recon.models import Disposition                             # noqa: E402
from recon.report import load_truth, score                       # noqa: E402


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    d = tmp_path_factory.mktemp("stub")
    write_dataset(d, seed=42, n_settlements=120)
    return d


def _run(d, scenario):
    findings, m, units, timing, agent = run(d, stub=scenario)
    return findings, agent, score(findings, load_truth(d))


def test_scripted_client_rejects_unknown_scenario():
    with pytest.raises(ValueError):
        ScriptedClient("wishful")


# --------------------------------------------------------------------------
# The safety property, one scenario at a time
# --------------------------------------------------------------------------

@pytest.mark.parametrize("scenario",
                         ["honest", "hallucinating", "overreaching",
                          "failing", "refusing", "plausible"])
def test_no_scenario_can_introduce_a_false_clear(dataset, scenario):
    """The headline invariant.

    Whatever the model does -- lies, overreaches, dies, or refuses -- the set of
    settlements escalated to a human must not shrink. The model has no authority
    over disposition, and this proves it across every failure mode.
    """
    _, _, metrics = _run(dataset, scenario)
    assert metrics["false_clear_count"] == 0


@pytest.mark.parametrize("scenario",
                         ["honest", "hallucinating", "overreaching",
                          "failing", "refusing", "plausible"])
def test_verdicts_are_identical_with_and_without_the_agent(dataset, scenario):
    """A model may improve the PROSE. It may not move a single verdict."""
    base, _, _, _, _ = run(dataset)
    after, _, _, _, _ = run(dataset, stub=scenario)

    base_v = {f.settlement_id: (f.disposition, f.reason_code) for f in base}
    after_v = {f.settlement_id: (f.disposition, f.reason_code) for f in after}
    assert base_v == after_v


def test_hallucinated_figures_are_all_caught(dataset):
    """Every note in this scenario contains an impossible figure."""
    findings, agent, _ = _run(dataset, "hallucinating")

    assert agent["narrations_accepted"] == 0
    assert any("invented_figure" in k for k in agent["guard"]["reasons"])
    assert agent["guard"]["rejection_rate"] == 1.0

    bad = IMPOSSIBLE_FIGURE.replace("Rs ", "")
    for f in findings:
        assert bad not in f.explanation
        assert bad not in f.action_required


def test_overreaching_match_proposals_are_all_rejected(dataset):
    """The model names a candidate confidently; arithmetic says no."""
    _, agent, _ = _run(dataset, "overreaching")
    assert agent["guard"]["reasons"].get("amount_mismatch", 0) > 0


@pytest.mark.parametrize("scenario", ["honest", "overreaching", "plausible"])
def test_a_match_proposal_can_never_place_a_bank_row(dataset, scenario):
    """The structural version of the invariant, and the one that matters.

    The five-scenario test above asserts that no verdict moves, and it passed
    for a bad reason: on this dataset the ambiguous set is empty and every
    orphan proposal failed on amount, so the code that ACCEPTED a proposal
    never ran in any scenario. An invariant guarded only by a path that never
    executes is not an invariant, it is a coincidence.

    A proposal now cannot reach a verdict by construction: it is returned as a
    lead, never written into the MatchResult, and the classifier is never re-run
    on model output. This asserts the mechanism rather than the outcome.
    """
    findings, agent, _ = _run(dataset, scenario)
    assert "matches_accepted" not in agent, (
        "matches are no longer accepted at all; a count of accepted matches "
        "means the placing path came back")
    for p in agent.get("proposals", []):
        assert p["settlement_id"]
    # Whatever was proposed, every finding still says a deterministic rule
    # decided it -- no disposition anywhere is attributed to the model.
    assert all(f.resolved_by.startswith("deterministic") for f in findings)


def test_an_ambiguous_row_is_never_resolved_by_the_model(tmp_path):
    """Two unpaid payouts, identical nets, one unreferenced credit.

    This is the exact input the resolver path exists for, and the one the main
    dataset never produces. Arithmetic genuinely cannot break the tie: both
    candidates are unpaid, both tie on amount, both sit inside the window. The
    old guard re-applied precisely those three tests and therefore accepted
    whichever candidate the model happened to name -- moving a verdict from
    exception to reconciled, and changing a reason code, on a coin flip.

    Both settlements must stay escalated, in every scenario.
    """
    _write_ambiguous_pair(tmp_path)
    base, _, _, _, _ = run(tmp_path)
    base_v = {f.settlement_id: (f.disposition, f.reason_code) for f in base}
    assert all(d is Disposition.EXCEPTION for d, _ in base_v.values()), base_v

    for scenario in SCENARIOS:
        after, _, _, _, agent = run(tmp_path, stub=scenario)
        after_v = {f.settlement_id: (f.disposition, f.reason_code) for f in after}
        assert after_v == base_v, (
            f"scenario {scenario!r} moved a verdict on an ambiguous row: "
            f"{base_v} -> {after_v}")


def test_a_dead_api_degrades_instead_of_exploding(dataset):
    """The books still have to close if the API is having a bad afternoon."""
    findings, agent, metrics = _run(dataset, "failing")
    assert agent["narrations_accepted"] == 0
    assert agent["usage"]["errors"] > 0
    assert metrics["false_clear_count"] == 0
    # Deterministic explanations survive untouched.
    assert all(f.explanation.strip() for f in findings)


def test_a_refusing_model_degrades_cleanly(dataset):
    findings, agent, metrics = _run(dataset, "refusing")
    assert agent["narrations_accepted"] == 0
    assert metrics["false_clear_count"] == 0


def _write_ambiguous_pair(d):
    """Two settlements with identical nets, and one credit that fits either."""
    import csv
    import json

    rows, nets = [], []
    for sid, utr, oid, pid in (("setl_AAA", "UTRAAA", "order_a", "pay_a"),
                               ("setl_BBB", "UTRBBB", "order_b", "pay_b")):
        amount, fee, tax = 120000, 2400, 432
        nets.append(amount - fee - tax)
        rows.append(dict(entity_id=f"trf_{sid}", type="payment", debit=0,
                         credit=amount - fee - tax, amount=amount,
                         currency="INR", fee=fee, tax=tax, settlement_id=sid,
                         settlement_utr=utr, created_at="2026-06-01",
                         settled_at="2026-06-02", payment_id=pid, order_id=oid,
                         method="upi", description="Payment"))
    _csv(d / "settlement_recon.csv", rows)
    _csv(d / "bank_statement.csv", [dict(
        txn_id="btxn_1", value_date="2026-06-02",
        narration="NEFT CR-RAZORPAY SOFTWARE PVT LTD-REF UNREADABLE",
        ref_no="", debit=0, credit=nets[0])])
    _csv(d / "order_ledger.csv", [
        dict(order_id="order_a", order_date="2026-06-01", customer_id="c1",
             gross_amount=120000, currency="INR", status="paid", payment_id="pay_a"),
        dict(order_id="order_b", order_date="2026-06-01", customer_id="c2",
             gross_amount=120000, currency="INR", status="paid", payment_id="pay_b")])
    (d / "ground_truth.json").write_text(json.dumps([
        dict(settlement_id="setl_AAA", true_class="true_mismatch",
             expected_disposition="exception", injected_delta=0,
             note="ambiguous", also_acceptable=[]),
        dict(settlement_id="setl_BBB", true_class="true_mismatch",
             expected_disposition="exception", injected_delta=0,
             note="ambiguous", also_acceptable=[]),
    ]), encoding="utf-8")


def _csv(path, rows):
    import csv
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_honest_notes_are_accepted_and_attributed(dataset):
    """The guard must not be so strict that nothing ever passes.

    A guard that rejects everything is trivially safe and completely useless,
    so the accept path needs a test as much as the reject path does.
    """
    findings, agent, _ = _run(dataset, "honest")
    assert agent["narrations_accepted"] > 0
    rewritten = [f for f in findings
                 if f.resolved_by == "deterministic+llm_narration"]
    assert len(rewritten) == agent["narrations_accepted"]
    # Provenance is recorded: you can always tell which prose a model touched.
    for f in rewritten:
        assert f.disposition is Disposition.EXCEPTION


def test_stub_runs_are_labelled_as_stubs(dataset):
    """A scripted run must never be mistakable for a measured one."""
    _, agent, _ = _run(dataset, "honest")
    assert agent["is_stub"] is True
    assert "SCRIPTED-STUB" in agent["model"]
