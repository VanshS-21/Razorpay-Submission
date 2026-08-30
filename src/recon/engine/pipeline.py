"""The reconciliation run: ingest -> match -> classify, with an optional agent pass.

The deterministic path runs first and runs alone by default. The agent layer is
strictly additive and strictly measured: it is invoked only on what arithmetic
could not settle, everything it returns is re-verified, and the run reports how
often it was called and how often it was overruled.

Ordering matters and is deliberate. Building the deterministic baseline before
the agent existed is what makes it possible to state what the agent actually
added, rather than asserting that it helps.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..ingest import load
from .classify import classify
from .matcher import match


def run(datadir: Path, use_llm: bool = False, model: str | None = None,
        narrate_limit: int | None = None, stub: str | None = None):
    """Reconcile a batch.

    Returns (findings, match_result, units, timing, agent_report). `agent_report`
    is None when the agent layer did not run.
    """
    t0 = time.perf_counter()
    units, bank, orders = load(datadir)
    t_load = time.perf_counter() - t0

    t1 = time.perf_counter()
    m = match(units, bank)
    t_match = time.perf_counter() - t1

    for sid, u in units.items():
        u.bank_credits = m.assigned.get(sid) or []

    order_index = {o.order_id: o for o in orders}

    t2 = time.perf_counter()
    findings = [classify(units[sid], m, order_index) for sid in units]
    t_classify = time.perf_counter() - t2

    agent_report = None
    if use_llm or stub:
        agent_report = _run_agent(findings, m, units, order_index,
                                  model, narrate_limit, stub)
        # Re-classify: a narration-resolved match changes the arithmetic, so the
        # verdict must be recomputed from the deterministic rules rather than
        # patched. The model never edits a disposition.
        if agent_report.get("matches_accepted"):
            for sid, u in units.items():
                u.bank_credits = m.assigned.get(sid) or []
            findings = [classify(units[sid], m, order_index) for sid in units]
            from ..agent.narrate import narrate_exceptions  # noqa: PLC0415
            agent_report["narrations_accepted"] = narrate_exceptions(
                findings, units, agent_report["_client"], agent_report["model"],
                agent_report["_usage"], agent_report["_stats"], narrate_limit)
        agent_report = _finalise_agent_report(agent_report, len(units))

    timing = {
        "load_s": round(t_load, 4),
        "match_s": round(t_match, 4),
        "classify_s": round(t_classify, 4),
        "total_s": round(time.perf_counter() - t0, 4),
        "settlements": len(units),
        "lines": sum(len(u.lines) for u in units.values()),
        "bank_rows": len(bank),
        "orders": len(orders),
    }
    return findings, m, units, timing, agent_report


def _run_agent(findings, m, units, order_index, model, narrate_limit, stub=None):
    from ..agent.guard import GuardStats
    from ..agent.llm import DEFAULT_MODEL, Usage, build_client
    from ..agent.narrate import narrate_exceptions
    from ..agent.resolve import resolve_unmatched

    model = model or DEFAULT_MODEL
    if stub:
        from ..agent.fake import ScriptedClient
        client = ScriptedClient(stub)
        model = f"SCRIPTED-STUB[{stub}]"   # never mistakable for a real model id
    else:
        client = build_client()      # raises LLMUnavailable; the CLI reports it
    usage = Usage(model=model)
    stats = GuardStats()

    t = time.perf_counter()
    matches = resolve_unmatched(m, units, client, model, usage, stats)
    narrations = narrate_exceptions(findings, units, client, model, usage,
                                    stats, narrate_limit)

    return {
        "model": model,
        "is_stub": bool(stub),
        "matches_accepted": matches,
        "narrations_accepted": narrations,
        "seconds": round(time.perf_counter() - t, 3),
        "_client": client,
        "_usage": usage,
        "_stats": stats,
    }


def _finalise_agent_report(rep, n_records: int) -> dict:
    usage, stats = rep.pop("_usage"), rep.pop("_stats")
    rep.pop("_client", None)
    rep["usage"] = usage.to_dict(n_records)
    rep["guard"] = stats.to_dict()
    return rep
