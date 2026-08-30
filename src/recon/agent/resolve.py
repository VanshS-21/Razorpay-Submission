"""Recover a settlement's identity from bank narration prose.

This is the fallback for bank rows the deterministic matcher could not place:
the reference field is unusable AND the amount does not uniquely identify a
payout. Reading "NEFT-RZRPY SOFT PVT-STLMNT-21390" and recognising an
abbreviated merchant name and a truncated UTR fragment is a language problem,
and it is the only part of matching where a model has an edge over a regex.

Measured honestly, that edge is currently worth very little. On the main
dataset the deterministic amount-and-date fallback resolves 100% of unusable-
reference rows on its own, so this path is invoked on almost nothing. That
finding is reported rather than hidden, and the residue is left small rather
than manufactured -- routing work through a model to justify its presence would
be the exact failure the architecture is meant to avoid.

Every proposal is re-verified arithmetically before acceptance. See guard.py.
"""

from __future__ import annotations

from ..models import rupees
from .guard import GuardStats, verify_match
from .llm import Usage, structured_call

SYSTEM = """You identify which settlement payout a bank statement line refers to.

You are given one bank statement row whose reference field is unusable, and a \
list of candidate payouts that have not yet been matched to any bank credit.

Use the narration text: merchant name spellings and abbreviations, truncated \
reference fragments, transfer type. Match those against the candidates.

You are NOT deciding whether the payout is settled. Your answer is a proposal \
that will be checked against the amount and the value date before it is used. \
If nothing in the narration points to a specific candidate, return null rather \
than guessing -- a wrong identification is worse than an unmatched row, because \
an unmatched row gets reviewed and a wrong one does not."""

SCHEMA = {
    "type": "object",
    "properties": {
        "settlement_id": {
            "type": ["string", "null"],
            "description": "Candidate id, or null if the narration is not decisive.",
        },
        "evidence": {
            "type": "string",
            "description": "The specific text in the narration that supports it.",
        },
        "confidence": {"type": "number", "description": "0.0 to 1.0"},
    },
    "required": ["settlement_id", "evidence", "confidence"],
    "additionalProperties": False,
}


def _candidates_block(units: dict, sids: list) -> str:
    out = []
    for sid in sids[:25]:
        u = units[sid]
        out.append(f"- {sid} | net {rupees(u.expected_net)} | "
                   f"settled {u.lines[0].settled_at} | utr {u.utr}")
    return "\n".join(out)


def resolve_unmatched(match_result, units: dict, client, model: str,
                      usage: Usage, stats: GuardStats) -> list[dict]:
    """Read orphan bank rows and PROPOSE which payout each belongs to.

    Returns a list of surviving proposals. It does not place them.

    This used to mutate the MatchResult exactly as a deterministic match would,
    on the reasoning that a proposal which passes the guard is one the engine
    could defend without mentioning a model. That reasoning is wrong in the one
    case the path exists for. Rows reach here precisely BECAUSE arithmetic could
    not identify them -- an ambiguous row is one where several unpaid
    settlements share the same net inside the same window, so re-applying the
    amount-and-date test cannot discriminate either. Every candidate passes, and
    whichever one the model named got cleared. The guard was checking a
    condition that was already true by construction.

    So the model no longer places anything. It reads narration prose, which is
    the thing it is genuinely better at than a regular expression, and its
    suggestion is attached to the exception for the human who has to act on it.
    A verdict is never moved. That is now true because the code cannot express
    the alternative, rather than because a test asserted it on data where the
    path never ran.
    """
    unmatched = [sid for sid in units if not match_result.assigned.get(sid)]
    if not unmatched:
        return []

    rows = list(match_result.orphan_bank) + [r for r, _ in match_result.ambiguous]
    if not rows:
        return []

    proposals: list[dict] = []
    for row in rows:
        still_open = [s for s in unmatched if not match_result.assigned.get(s)]
        if not still_open:
            break

        prompt = (
            f"Bank row:\n"
            f"  value date : {row.value_date}\n"
            f"  narration  : {row.narration}\n"
            f"  reference  : {row.ref_no or '(blank)'}\n"
            f"  credit     : {rupees(row.credit)}\n"
            f"  debit      : {rupees(row.debit)}\n\n"
            f"Unmatched candidate payouts:\n{_candidates_block(units, still_open)}\n\n"
            f"Which candidate does this row refer to?"
        )
        out = structured_call(client, model, SYSTEM, prompt, SCHEMA, usage)
        if not out or not out.get("settlement_id"):
            continue

        if not verify_match(out, units, row, stats):
            continue

        proposals.append({
            "settlement_id": out["settlement_id"],
            "txn_id": row.txn_id,
            "value_date": row.value_date,
            "narration": row.narration,
            "credit": row.credit,
            "debit": row.debit,
        })

    return proposals
