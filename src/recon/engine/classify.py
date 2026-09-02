"""Turn a matched settlement into a verdict.

Rules are ordered from most certain to least, and the last rule is deliberately
the pessimistic one: anything this module cannot positively explain becomes an
exception. A reconciliation engine's default must be "I don't know", because the
alternative default -- silently clearing what it failed to understand -- is the
error that destroys trust in the whole system.

Two classes can never be auto-cleared no matter what else is true:
DUPLICATE_BANK_CREDIT and TRUE_MISMATCH. See models.MUST_ESCALATE.
"""

from __future__ import annotations

from ..models import (
    AnomalyClass,
    Disposition,
    EntityType,
    Finding,
    ReconUnit,
    rupees,
)
from .arithmetic import (
    DATE_WINDOW_DAYS,
    line_drift,
    looks_like_bank_charge,
    within_rounding,
)
from .matcher import MatchResult, _days_apart

#: A refund raised this many days before the payout it was netted into is a
#: late refund rather than a same-cycle one.
LATE_REFUND_DAYS = 4


def _has(lines, t: EntityType) -> bool:
    return any(l.type is t for l in lines)


#: Words a bank uses when it itemises a fee. Narrow on purpose: this list is the
#: difference between "reconciled" and "escalated", so it must not stretch to
#: cover anything vaguely fee-shaped.
CHARGE_WORDS = ("CHARGE", "CHARGES", "COMMISSION", "FEE")


def _is_charge_narration(narration: str) -> bool:
    n = (narration or "").upper()
    return any(w in n for w in CHARGE_WORDS)


def _duplicate_rows(rows) -> bool:
    """Two bank rows with the same reference AND the same amount.

    That combination does not occur naturally: a bank does not send the same
    amount under the same UTR twice unless something went wrong on its side.
    """
    seen = set()
    for r in rows:
        key = (r.ref_no, r.credit - r.debit)
        if key in seen:
            return True
        seen.add(key)
    return False


def ledger_mismatches(unit: ReconUnit, orders: dict) -> list:
    """Payment lines whose amount disagrees with the merchant's order ledger.

    This is the only check in the engine that uses the third source, and it is
    the only one that can catch an error which nets to zero across a settlement.
    Everything else compares two totals; this compares every line to what the
    business says it actually sold.

    Only lines the ledger can actually be compared against are checked here; a
    line naming an order the ledger has never heard of is not a disagreement
    about an amount, it is an absence of corroboration, and
    `uncorroborated_lines` handles it.
    """
    out = []
    for l in unit.lines:
        if l.type is not EntityType.PAYMENT or not l.order_id:
            continue
        o = orders.get(l.order_id)
        if o is not None and o.gross_amount != l.amount:
            out.append((l, o))
    return out


def uncorroborated_lines(unit: ReconUnit, orders: dict) -> list:
    """Lines that move money against an order the merchant's books do not have.

    Every rule above this one compares a line to an order record. All of them
    therefore skip a line with no usable `order_id` -- and skipping is the same
    as clearing, because the line's debit still flows into `expected_net`, so the
    bank still ties to the payout and the settlement reads CLEAN.

    That left two holes wide enough to drive a fraud through. A refund with the
    `order_id` column left empty walked straight past `unsupported_refunds`, the
    single defect this project advertises most: Rs 5,000 debited, nothing in the
    ledger, delta 0, "false-clear rate 0.0% [PASS]". And ADJUSTMENT was in the
    taxonomy with no rule anywhere that inspected one, so an adjustment line of
    any size was absorbed into the net without a single check.

    The rule is the same one the whole engine rests on, applied to every line
    type rather than to the two the generator happens to emit: a line that moves
    money must name an order the books know about. A blank id is less
    corroborated than a wrong one, not more.
    """
    out = []
    for l in unit.lines:
        if not l.order_id or orders.get(l.order_id) is None:
            out.append(l)
    return out


def internally_inconsistent_lines(unit: ReconUnit) -> list:
    """Payment lines whose own components do not add up.

    credit must equal amount - fee - tax. When it does not, the PSP's own row
    disagrees with itself, and every total built from it inherits the error --
    including the expected net the whole reconciliation is measured against.

    `line_drift` computed exactly this and had no callers, so a settlement
    whose lines were internally contradictory reconciled normally as long as
    the contradictions summed to the credit. Checked here so a row that does
    not add up is escalated rather than trusted.
    """
    return [(l, line_drift(l)) for l in unit.lines if line_drift(l) != 0]


def unsupported_refunds(unit: ReconUnit, orders: dict) -> list:
    """Refund lines the merchant's books do not corroborate.

    Added after the adversarial holdout showed the engine clearing four phantom
    refunds. The original ledger check only compared PAYMENT lines against the
    order ledger -- so money coming IN was verified three ways and money going
    OUT was verified against the bank total alone. Since a refund reduces both
    the payout and the bank credit identically, it always ties, and a refund for
    an order that was never refunded is invisible to every totals-based check.

    That is the shape of a misposted or fraudulent refund, so it escalates.

    A refund whose `order_id` is blank or unknown is handled by
    `uncorroborated_lines`, which runs first -- it is a stronger finding, not a
    weaker one.
    """
    out = []
    for l in unit.lines:
        if l.type is not EntityType.REFUND or not l.order_id:
            continue
        o = orders.get(l.order_id)
        if o is None:
            out.append((l, None))
        elif o.status not in ("refunded", "partially_refunded"):
            out.append((l, o))
    return out


def classify(unit: ReconUnit, m: MatchResult, orders: dict | None = None) -> Finding:
    sid = unit.settlement_id
    rows = m.assigned.get(sid) or []
    method = m.method.get(sid, "none")
    group = m.group.get(sid)
    lines = unit.lines

    def finding(cls, disp, why, action="", by="deterministic", conf=1.0, delta=None):
        return Finding(
            settlement_id=sid,
            utr=unit.utr,
            disposition=disp,
            reason_code=cls,
            delta=unit.delta if delta is None else delta,
            explanation=why,
            action_required=action,
            resolved_by=by,
            confidence=conf,
        )

    # --- 1. duplicate credit -------------------------------------------------
    # Checked first and unconditionally. This is unearned money that the bank
    # will reverse; clearing it lets the merchant spend funds that are about to
    # disappear. It outranks every other explanation.
    if _duplicate_rows(rows):
        dup = sum(r.credit - r.debit for r in rows) - unit.expected_net
        return finding(
            AnomalyClass.DUPLICATE_BANK_CREDIT,
            Disposition.EXCEPTION,
            f"Bank credited UTR {unit.utr} twice; {rupees(dup)} of the balance "
            f"is unearned and will be reversed.",
            action="Do not treat as available balance. Raise a duplicate-credit "
                   "query with the bank quoting both transaction references.",
            delta=dup,
        )

    # --- 1b. line-level disagreement with the order ledger -------------------
    # Checked before any total-based rule, because the whole point of this class
    # is that the totals look perfect. An engine that reaches the "amount ties
    # exactly" branch first would clear it and never know.
    if orders:
        ghost = uncorroborated_lines(unit, orders)
        if ghost:
            total = sum(l.debit + l.credit for l in ghost)
            kinds = ", ".join(sorted({l.type.value for l in ghost}))
            named = ", ".join(l.order_id or "(no order id)" for l in ghost[:3])
            return finding(
                AnomalyClass.LEDGER_MISMATCH,
                Disposition.EXCEPTION,
                f"{len(ghost)} line(s) totalling {rupees(total)} ({kinds}) move "
                f"money against order(s) the merchant's ledger does not have "
                f"({named}). The settlement still ties to the bank, because the "
                f"line is inside the payout on both sides -- the books are the "
                f"only source that can see it at all.",
                action="Identify what each line is for before closing. A line "
                       "with no order behind it is either a misposting or money "
                       "moving on an instruction the books never recorded.",
            )

        phantom = unsupported_refunds(unit, orders)
        if phantom:
            total = sum(l.debit for l, _ in phantom)
            ids = ", ".join(l.order_id for l, _ in phantom[:3])
            return finding(
                AnomalyClass.LEDGER_MISMATCH,
                Disposition.EXCEPTION,
                f"{len(phantom)} refund(s) totalling {rupees(total)} were "
                f"debited from this payout for order(s) the ledger still records "
                f"as not refunded ({ids}). The settlement ties to the bank "
                f"exactly, because the refund reduces both sides equally -- the "
                f"books are the only source that disagrees.",
                action="Verify each refund against the order record before "
                       "closing. An uncorroborated refund is either a misposting "
                       "or money leaving on an instruction nobody authorised.",
            )

        bad = ledger_mismatches(unit, orders)
        if bad:
            worst = max(bad, key=lambda p: abs(p[0].amount - p[1].gross_amount))
            net_off = sum(l.amount - o.gross_amount for l, o in bad)
            return finding(
                AnomalyClass.LEDGER_MISMATCH,
                Disposition.EXCEPTION,
                f"{len(bad)} payment line(s) disagree with the order ledger; "
                f"largest is {worst[0].order_id} booked at "
                f"{rupees(worst[0].amount)} against a ledger value of "
                f"{rupees(worst[1].gross_amount)}. The errors net to "
                f"{rupees(net_off)} across the settlement, so the payout total "
                f"and the bank credit both still tie.",
                action="Reconcile each flagged order against the source invoice "
                       "before closing. The settlement total is not evidence "
                       "that the lines are right.",
            )

    # --- 2. consolidated transfer -------------------------------------------
    # The only rule here that clears a payout on arithmetic ALONE. Nothing in
    # the statement names the other members of the group: the bank quoted one
    # UTR and the rest is inferred from the fact that their nets add up. That
    # inference is almost always right and is the reason a subset-sum solver
    # has to exist at all -- but "these payouts sum to this credit" and "this
    # credit paid these payouts" are not the same statement, and an over-credit
    # sitting beside an unrelated unpaid payout of the right size satisfies the
    # first without satisfying the second.
    #
    # It stays RECONCILED, because escalating every consolidated transfer would
    # bury the exception list in the one case the solver was built for. It is
    # marked as inferred, carries reduced confidence, and is listed for spot
    # check so the judgement is visible rather than silent. See the limitations
    # section of the README.
    if method == "subset_sum" and group:
        return finding(
            AnomalyClass.CONSOLIDATED_PAYOUT,
            Disposition.RECONCILED,
            f"Paid in a single bank transfer jointly with {', '.join(group)}; "
            f"the combined nets reconcile exactly. Group membership is INFERRED "
            f"from the amounts -- the statement names only one UTR.",
            action="Spot-check: confirm with the bank that this transfer covers "
                   "the grouped payouts.",
            by="deterministic:inferred",
            conf=0.9,
            delta=0,
        )

    # --- 2b. the PSP's own rows do not add up --------------------------------
    drifted = internally_inconsistent_lines(unit)
    if drifted:
        worst = max(drifted, key=lambda t: abs(t[1]))
        return finding(
            AnomalyClass.LEDGER_MISMATCH,
            Disposition.EXCEPTION,
            f"{len(drifted)} settlement line(s) do not add up internally: "
            f"{worst[0].entity_id} states a credit of {rupees(worst[0].credit)} "
            f"but amount minus fee minus tax gives "
            f"{rupees(worst[0].credit - worst[1])}.",
            action="Re-fetch the settlement report from the PSP. Every total "
                   "derived from these rows, including the expected net, is "
                   "unreliable until they agree.",
        )

    # --- 3. never credited ---------------------------------------------------
    if not rows:
        return finding(
            AnomalyClass.TRUE_MISMATCH,
            Disposition.EXCEPTION,
            f"No bank credit found for this payout of {rupees(unit.expected_net)} "
            f"under UTR {unit.utr}, and it is not part of a consolidated transfer.",
            action="Confirm with the PSP that the payout was released, then trace "
                   "the UTR with the bank.",
            delta=-unit.expected_net,
        )

    delta = unit.delta

    # --- 4. the amount ties exactly -----------------------------------------
    if delta == 0:
        if _has(lines, EntityType.DISPUTE):
            disp_lines = [l for l in lines if l.type is EntityType.DISPUTE]
            total = sum(l.debit for l in disp_lines)
            return finding(
                AnomalyClass.CHARGEBACK_DEDUCTION,
                Disposition.RECONCILED,
                f"Payout is net of {len(disp_lines)} chargeback deduction(s) "
                f"totalling {rupees(total)}, including handling fees. "
                f"All three sources agree once the disputes are accounted for.",
            )

        refunds = [l for l in lines if l.type is EntityType.REFUND]
        if refunds:
            partial = [l for l in refunds if "Partial" in l.description]
            if partial:
                return finding(
                    AnomalyClass.SPLIT_REFUND,
                    Disposition.RECONCILED,
                    f"Contains {len(partial)} partial refund(s) belonging to "
                    f"order(s) refunded across more than one payout. The payout "
                    f"ties once the partial amounts are netted.",
                )
            late = [l for l in refunds
                    if _days_apart(l.created_at, l.settled_at) > LATE_REFUND_DAYS]
            if late:
                return finding(
                    AnomalyClass.REFUND_NETTED_LATER,
                    Disposition.RECONCILED,
                    f"Includes {len(late)} refund(s) raised in an earlier period "
                    f"but netted into this payout. Ties exactly once the timing "
                    f"of the refund is accounted for.",
                )

        if method == "amount_date":
            return finding(
                AnomalyClass.MISSING_UTR,
                Disposition.RECONCILED,
                f"Bank reference field was unusable; identified by exact net "
                f"amount {rupees(unit.expected_net)} within the settlement "
                f"window. Amount and date agree.",
                conf=0.95,
            )

        settled = lines[0].settled_at
        if any(_days_apart(settled, r.value_date) > 0 for r in rows):
            gap = max(_days_apart(settled, r.value_date) for r in rows)
            return finding(
                AnomalyClass.TIMING_CUT,
                Disposition.RECONCILED,
                f"Correct amount, credited {gap} day(s) after the settlement "
                f"date. Crosses the period cut-off but is not a discrepancy.",
            )

        return finding(
            AnomalyClass.CLEAN,
            Disposition.RECONCILED,
            "Payout, bank credit and order ledger agree exactly.",
        )

    # --- 5. small differences with a known cause -----------------------------
    if within_rounding(delta):
        return finding(
            AnomalyClass.FEE_TAX_ROUNDING,
            Disposition.RECONCILED,
            f"Difference of {delta} paise from per-line fee and GST truncation "
            f"across {len(lines)} lines. Within the {5}-paise arithmetic "
            f"tolerance.",
        )

    # A shortfall is only written off as a bank charge when the statement
    # ITEMISES it. Magnitude alone is not evidence: an unexplained Rs 23 short
    # and a Rs 23 transfer fee look identical on the bottom line, and the
    # generator overlaps the two ranges precisely so that guessing from size
    # cannot work. Corroboration or escalation -- there is no third option.
    if delta < 0:
        charge_rows = [r for r in rows
                       if r.debit == -delta and _is_charge_narration(r.narration)]
        if charge_rows:
            return finding(
                AnomalyClass.BANK_CHARGE_ADJUSTMENT,
                Disposition.RECONCILED,
                f"Credit is short by {rupees(-delta)}, itemised on the statement "
                f'as "{charge_rows[0].narration}". Payout ties once the charge '
                f"is accounted for.",
            )
        if looks_like_bank_charge(delta):
            return finding(
                AnomalyClass.TRUE_MISMATCH,
                Disposition.EXCEPTION,
                f"Credit is short by {rupees(-delta)}. The amount is small enough "
                f"to be a transfer charge, but the statement carries no charge "
                f"entry for it, so it cannot be written off on size alone.",
                action="Ask the bank to itemise the deduction against this UTR. "
                       "If no charge was levied, this is a genuine shortfall.",
            )

    # --- 6. everything else --------------------------------------------------
    # Nothing explains this. Refusing to clear it is the correct behaviour and
    # the single most important line in the file.
    direction = "more" if delta > 0 else "less"
    return finding(
        AnomalyClass.TRUE_MISMATCH,
        Disposition.EXCEPTION,
        f"Bank credited {rupees(abs(delta))} {direction} than the payout of "
        f"{rupees(unit.expected_net)}, and no refund, chargeback, rounding or "
        f"bank charge in any source accounts for it.",
        action=f"Investigate before the books are closed. Compare the PSP payout "
               f"advice for {unit.utr} against the bank statement line and "
               f"identify the {rupees(abs(delta))} difference.",
    )
