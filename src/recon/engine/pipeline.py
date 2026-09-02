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
        narrate_limit: int | None = None, stub: str | None = None,
        provider: str | None = None):
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
                                  model, narrate_limit, stub, provider)
        # No re-classification, because nothing the model returned changed the
        # arithmetic. Its identity suggestions are attached to the exceptions
        # they concern, as a lead for the human, and the verdict stands exactly
        # as the deterministic engine left it.
        _attach_proposals(findings, agent_report.get("proposals") or [])
        # A capped run cannot be extrapolated to a whole batch; see
        # Usage.per_n_records for what that mistake looked like.
        n_exceptions = sum(1 for f in findings
                           if f.disposition.value == "exception")
        complete = narrate_limit is None or narrate_limit >= n_exceptions
        agent_report["exceptions_total"] = n_exceptions
        agent_report = _finalise_agent_report(agent_report, len(units),
                                              complete=complete)

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


def _attach_proposals(findings, proposals):
    """Hand the model's identity suggestions to the human, as leads only.

    A proposal is added to `action_required` and nowhere else. It cannot reach
    `disposition`, `reason_code` or `delta`, because it is never given to the
    classifier -- which is the strongest form the guarantee can take: not a
    check that the model behaved, but no code path by which it could not.
    """
    if not proposals:
        return
    by_sid = {}
    for p in proposals:
        by_sid.setdefault(p["settlement_id"], []).append(p)
    for f in findings:
        for p in by_sid.get(f.settlement_id, []):
            f.action_required = (
                f"{f.action_required} "
                f"LEAD (model-suggested, unverified): bank row {p['txn_id']} on "
                f"{p['value_date']} -- \"{p['narration']}\" -- ties on amount and "
                f"date. Confirm with the bank before treating it as settled."
            ).strip()


def _run_agent(findings, m, units, order_index, model, narrate_limit,
               stub=None, provider=None):
    from ..agent.guard import GuardStats
    from ..agent.llm import (
        DEFAULT_MODELS,
        AnthropicBackend,
        Usage,
        build_client,
    )
    from ..agent.narrate import narrate_exceptions
    from ..agent.resolve import resolve_unmatched

    if stub:
        # The scripted client mimics the Anthropic wire shape, so it goes behind
        # the same backend as the real thing -- the stub exercises the actual
        # code path rather than a parallel one built for testing.
        from ..agent.fake import ScriptedClient
        client = AnthropicBackend(ScriptedClient(stub))
        # The scripted client borrows the Anthropic wire shape, but no vendor
        # answered. Printing "provider anthropic" beside SCRIPTED-STUB[...] is
        # a real-looking value describing something that did not happen.
        client.provider = f"none (scripted stub, wire shape: anthropic)"
        model = f"SCRIPTED-STUB[{stub}]"   # never mistakable for a real model id
    else:
        # raises LLMUnavailable; the CLI reports it
        client = build_client(provider)
        model = model or DEFAULT_MODELS[client.provider]
    usage = Usage(model=model)
    stats = GuardStats()

    t = time.perf_counter()
    proposals = resolve_unmatched(m, units, client, model, usage, stats)
    narrations = narrate_exceptions(findings, units, client, model, usage,
                                    stats, narrate_limit)

    return {
        "model": model,
        "provider": client.provider,
        "is_stub": bool(stub),
        "proposals": proposals,
        "matches_proposed": len(proposals),
        "narrations_accepted": narrations,
        "seconds": round(time.perf_counter() - t, 3),
        "_client": client,
        "_usage": usage,
        "_stats": stats,
    }


def _finalise_agent_report(rep, n_records: int, complete: bool = True) -> dict:
    usage, stats = rep.pop("_usage"), rep.pop("_stats")
    rep.pop("_client", None)
    rep["usage"] = usage.to_dict(n_records, complete=complete)
    rep["guard"] = stats.to_dict()
    return rep
