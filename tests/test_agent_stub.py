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

from recon.agent.fake import IMPOSSIBLE_FIGURE, ScriptedClient   # noqa: E402
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
                          "failing", "refusing"])
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
                          "failing", "refusing"])
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
    assert agent["matches_accepted"] == 0
    assert agent["guard"]["reasons"].get("amount_mismatch", 0) > 0


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
