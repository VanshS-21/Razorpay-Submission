"""Verification of everything a language model produces.

The rule this module enforces: **the model proposes, arithmetic disposes.**

Nothing a model returns is trusted on its own. A proposed match is only accepted
if it satisfies the same exact-amount and date-window test the deterministic
matcher would have applied. Generated prose is only accepted if every monetary
figure in it appears in the set of numbers the engine actually computed.

That second check is the one that matters. A model writing an exception note is
being asked to describe money, and a plausible wrong number in a finance
document is worse than no note at all -- it is a number a human will act on. So
the guard extracts every figure from the generated text and rejects the whole
output if even one was invented, falling back to the deterministic explanation.

Rejections are counted, not hidden. The guard rejection rate is reported as a
metric, because how often the model had to be overruled is a fact about the
system worth publishing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..engine.arithmetic import DATE_WINDOW_DAYS
from ..engine.matcher import _days_apart

#: Rs 1,234.56 / Rs 1234.56 / Rs 12,34,567.89 -- any figure the model may write.
_MONEY = re.compile(r"Rs\s*([\d,]+(?:\.\d{1,2})?)")


@dataclass
class GuardStats:
    """Tallies of what the guard let through and what it caught."""

    checked: int = 0
    rejected: int = 0
    reasons: dict = field(default_factory=dict)

    def reject(self, why: str):
        self.rejected += 1
        self.reasons[why] = self.reasons.get(why, 0) + 1

    @property
    def rejection_rate(self) -> float:
        return self.rejected / self.checked if self.checked else 0.0

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "rejected": self.rejected,
            "rejection_rate": round(self.rejection_rate, 4),
            "reasons": dict(self.reasons),
        }


def _figures(text: str) -> set[str]:
    """Every rupee figure in a string, normalised for comparison."""
    out = set()
    for raw in _MONEY.findall(text or ""):
        cleaned = raw.replace(",", "")
        if "." not in cleaned:
            cleaned += ".00"
        whole, frac = cleaned.split(".")
        out.add(f"{int(whole)}.{(frac + '00')[:2]}")
    return out


def verify_narration(text: str, allowed_paise: set[int],
                     stats: GuardStats) -> bool:
    """True if every rupee figure in `text` is one the engine actually computed.

    `allowed_paise` is the set of integer-paise values the engine derived for
    this settlement. A figure outside it means the model produced a number that
    exists nowhere in the source data.
    """
    stats.checked += 1
    allowed = set()
    for p in allowed_paise:
        v = abs(int(p))
        allowed.add(f"{v // 100}.{v % 100:02d}")

    found = _figures(text)
    invented = found - allowed
    if invented:
        stats.reject(f"invented_figure:{sorted(invented)[0]}")
        return False
    return True


def verify_match(proposal: dict, units: dict, row, stats: GuardStats) -> bool:
    """True if a model-proposed bank-row-to-settlement match survives arithmetic.

    The model is allowed to read narration prose and suggest an identity. It is
    not allowed to decide that a payout is settled. The suggestion is only
    accepted when the amount ties exactly and the date falls inside the same
    window the deterministic matcher uses -- which means an accepted proposal is
    one the engine could defend without ever mentioning a model.
    """
    stats.checked += 1
    sid = (proposal or {}).get("settlement_id")

    if not sid:
        stats.reject("no_settlement_proposed")
        return False
    if sid not in units:
        stats.reject("unknown_settlement_id")
        return False

    u = units[sid]
    if u.bank_credits:
        stats.reject("settlement_already_matched")
        return False
    if u.expected_net != row.credit - row.debit:
        stats.reject("amount_mismatch")
        return False
    if _days_apart(u.lines[0].settled_at, row.value_date) > DATE_WINDOW_DAYS:
        stats.reject("outside_date_window")
        return False
    return True


def allowed_figures_for(unit, finding) -> set[int]:
    """The paise values a narration about this settlement may legitimately cite."""
    vals = {
        abs(unit.expected_net),
        abs(unit.observed_net),
        abs(unit.delta),
        abs(finding.delta),
    }
    for l in unit.lines:
        vals.update({abs(l.amount), abs(l.credit), abs(l.debit),
                     abs(l.fee), abs(l.tax)})
    for b in unit.bank_credits:
        vals.update({abs(b.credit), abs(b.debit)})
    vals.discard(0)
    return vals
