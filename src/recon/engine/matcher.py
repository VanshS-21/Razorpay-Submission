"""Assign bank credits to settlements.

Three passes, cheapest and most certain first:

  1. exact UTR join            -- the happy path, and most of the volume
  2. exact amount + date window-- for rows whose reference field is unusable
  3. bounded subset-sum        -- for one credit covering several payouts

Pass 3 is the one that matters conceptually. A bank does not credit per payment,
it credits per transfer, and a transfer may sweep up several payouts. So the
question "which settlement is this credit for?" has no answer in general -- the
right question is "which SUBSET of settlements does this credit cover?", and
that is a search, not a lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

from ..models import BankCredit, ReconUnit
from .arithmetic import DATE_WINDOW_DAYS


#: Kept in sync with classify.CHARGE_WORDS; a charge row is a fee on the
#: transfer rather than part of the payout it carries.
_CHARGE_WORDS = ("CHARGE", "CHARGES", "COMMISSION", "FEE")


def _is_charge(narration: str) -> bool:
    n = (narration or "").upper()
    return any(w in n for w in _CHARGE_WORDS)


def _d(iso: str) -> date:
    y, m, dd = iso.split("-")
    return date(int(y), int(m), int(dd))


def _days_apart(a: str, b: str) -> int:
    return abs((_d(a) - _d(b)).days)


@dataclass
class MatchResult:
    """Which bank rows belong to which settlements, and how we decided."""

    #: settlement_id -> bank rows assigned to it
    assigned: dict = field(default_factory=dict)
    #: settlement_id -> how it was matched (utr | amount_date | subset_sum | none)
    method: dict = field(default_factory=dict)
    #: settlement_id -> the other settlements sharing its bank credit
    group: dict = field(default_factory=dict)
    #: bank rows nothing could be found for
    orphan_bank: list = field(default_factory=list)
    #: settlements with no bank credit at all
    unpaid: list = field(default_factory=list)
    #: settlements whose reference was unusable and needed a fallback
    needed_fallback: set = field(default_factory=set)
    #: rows pass 2 could not place -- the residue offered to the language model
    ambiguous: list = field(default_factory=list)


def subset_summing_to(target: int, candidates: list[tuple[str, int]],
                      max_size: int = 3) -> list[str] | None:
    """Find up to `max_size` candidates whose values sum exactly to `target`.

    Exhaustive over a deliberately tiny candidate set (settlements that are
    unpaid and within the date window), so the combinatorics stay trivial and
    the answer is exact. Approximate subset-sum has no place here: "these three
    payouts approximately equal this credit" is not a reconciliation.

    Returns the settlement ids, or None. Exact-match only, no tolerance --
    a consolidated transfer is the arithmetic sum of its parts.
    """
    if target <= 0 or not candidates:
        return None
    for size in range(1, min(max_size, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            if sum(v for _, v in combo) == target:
                return [k for k, _ in combo]
    return None


def match(units: dict, bank_rows: list) -> MatchResult:
    res = MatchResult()
    for sid in units:
        res.assigned[sid] = []
        res.method[sid] = "none"

    # ---- pass 1: exact UTR join -------------------------------------------
    by_utr: dict[str, list[str]] = {}
    for sid, u in units.items():
        by_utr.setdefault(u.utr, []).append(sid)

    leftover: list[BankCredit] = []
    for row in bank_rows:
        sids = by_utr.get(row.ref_no) if row.ref_no else None
        if sids:
            # A UTR identifies one payout; if it somehow maps to several, the
            # first is taken and the duplicate surfaces as an amount discrepancy
            # rather than being silently spread across settlements.
            sid = sids[0]
            res.assigned[sid].append(row)
            res.method[sid] = "utr"
        else:
            leftover.append(row)

    # ---- pass 2: exact amount + date window --------------------------------
    # Only settlements still without a credit are eligible, so a row can never
    # be stolen from a settlement that already joined cleanly on its UTR.
    unmatched_sids = [sid for sid in units if not res.assigned[sid]]

    for row in list(leftover):
        hits = []
        for sid in unmatched_sids:
            u = units[sid]
            if res.assigned[sid]:
                continue
            if u.expected_net != row.credit - row.debit:
                continue
            settled = u.lines[0].settled_at
            if _days_apart(settled, row.value_date) > DATE_WINDOW_DAYS:
                continue
            hits.append(sid)

        if len(hits) == 1:
            sid = hits[0]
            res.assigned[sid].append(row)
            res.method[sid] = "amount_date"
            res.needed_fallback.add(sid)
            leftover.remove(row)
        elif len(hits) > 1:
            # Genuinely ambiguous: several unpaid settlements have the identical
            # net within the window. Arithmetic cannot break the tie, so the row
            # is handed to the narration resolver instead of guessed at.
            res.ambiguous.append((row, hits))

    # ---- pass 3: bounded subset-sum ----------------------------------------
    # A credit that joined on UTR but exceeds that settlement's net may be a
    # consolidated transfer. Look for unpaid settlements nearby whose nets make
    # up exactly the surplus.
    for sid, u in units.items():
        rows = res.assigned.get(sid) or []
        if not rows:
            continue
        # Charges are a fee ON the transfer, not part of the payout being
        # transferred, so they are excluded before asking "what else does this
        # credit cover?". Including them made a Rs 23 charge on a consolidated
        # transfer break the exact-sum requirement and report both payouts as
        # never paid -- found by the adversarial holdout.
        surplus = sum(r.credit for r in rows) - sum(
            r.debit for r in rows if not _is_charge(r.narration)) - u.expected_net
        if surplus <= 0:
            continue

        anchor_date = u.lines[0].settled_at
        candidates = [
            (osid, ou.expected_net)
            for osid, ou in units.items()
            if osid != sid
            and not res.assigned.get(osid)
            and _days_apart(anchor_date, ou.lines[0].settled_at) <= DATE_WINDOW_DAYS
        ]
        found = subset_summing_to(surplus, candidates)
        if found:
            members = [sid] + found
            for m in members:
                res.method[m] = "subset_sum"
                res.group[m] = [x for x in members if x != m]

    res.orphan_bank = [r for r in leftover
                       if r not in [a for a, _ in res.ambiguous]]
    res.unpaid = [sid for sid in units
                  if not res.assigned[sid] and sid not in res.group]
    return res
