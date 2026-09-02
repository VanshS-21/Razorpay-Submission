"""Rewrite exception notes for the human who has to act on them.

This is the one job in the pipeline where a language model is unambiguously the
right tool. The deterministic classifier produces correct but templated prose --
it knows the facts, not how to brief a finance analyst on a Monday morning. The
model is given the computed facts and asked to turn them into an explanation and
a concrete next action.

Hard boundaries, enforced by the guard:

  - it may NOT change the disposition. Whether something is an exception is an
    arithmetic decision, already made.
  - it may NOT change the reason code.
  - it may NOT introduce a monetary figure the engine did not compute.

So the model is a writer here, not a decision maker. If it violates any of
those, the deterministic text is kept and the rejection is counted.
"""

from __future__ import annotations

from ..models import Disposition, Finding, rupees
from .guard import GuardStats, allowed_figures_for, verify_narration
from .llm import Usage, structured_call

SYSTEM = """You write exception notes for a settlement reconciliation system \
used by a finance team.

You are given facts that have already been established by exact integer \
arithmetic. Your job is to explain the situation to an analyst and tell them \
what to do next.

Rules, without exception:
- Use ONLY the figures given to you. Never compute, estimate, round, or infer a \
new monetary amount. Every rupee figure you write must appear verbatim in the \
facts provided.
- Do not speculate about causes the facts do not support.
- Do not reassure. If money is missing, say so plainly.
- The action must be something a person can actually do: who to contact, what \
reference to quote, what to compare.
- Be brief. Two or three sentences of explanation, one action."""

SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {
            "type": "string",
            "description": "Two or three sentences stating what is wrong.",
        },
        "action_required": {
            "type": "string",
            "description": "One concrete next step for a human.",
        },
    },
    "required": ["explanation", "action_required"],
    "additionalProperties": False,
}


def _facts(unit, finding: Finding) -> str:
    lines = [
        f"settlement_id: {finding.settlement_id}",
        f"bank UTR: {finding.utr}",
        f"reason code (already decided, do not change): {finding.reason_code.value}",
        f"expected payout: {rupees(unit.expected_net)}",
        f"bank credited: {rupees(unit.observed_net)}",
        f"difference: {rupees(finding.delta)}",
        f"settlement lines: {len(unit.lines)}",
        f"deterministic finding: {finding.explanation}",
    ]
    kinds = {}
    for l in unit.lines:
        kinds[l.type.value] = kinds.get(l.type.value, 0) + 1
    lines.append("line composition: " + ", ".join(f"{v} {k}" for k, v in kinds.items()))
    for b in unit.bank_credits:
        lines.append(f"bank row: {b.value_date} | {b.narration} | "
                     f"credit {rupees(b.credit)} | debit {rupees(b.debit)}")
    return "\n".join(lines)


def narrate_exceptions(findings: list, units: dict, client, model: str,
                       usage: Usage, stats: GuardStats,
                       limit: int | None = None) -> int:
    """Rewrite the notes on exception findings in place. Returns count accepted.

    Only exceptions are sent. Reconciled units need no human-facing prose, so
    calling the model on them would be spending money to restate "this is fine".
    """
    targets = [f for f in findings if f.disposition is Disposition.EXCEPTION]
    # `if limit:` treated 0 as "no cap", so --narrate-limit 0 narrated the whole
    # batch: asking for zero calls billed for nineteen.
    if limit is not None:
        targets = targets[:limit]

    accepted = 0
    for f in targets:
        unit = units.get(f.settlement_id)
        if unit is None:
            continue

        out = structured_call(
            client, model, SYSTEM,
            "Facts:\n" + _facts(unit, f) + "\n\nWrite the exception note.",
            SCHEMA, usage)
        if not out:
            continue

        allowed = allowed_figures_for(unit, f)
        blob = f"{out.get('explanation', '')} {out.get('action_required', '')}"
        if not verify_narration(blob, allowed, stats):
            # Guard caught an invented figure. Keep the deterministic text.
            continue

        f.explanation = out["explanation"].strip()
        f.action_required = out["action_required"].strip()
        f.resolved_by = "deterministic+llm_narration"
        accepted += 1

    return accepted
