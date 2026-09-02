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

#: Every ordinary way of writing an Indian rupee figure. The original pattern
#: was `Rs\s*([\d,]+...)` and claimed to catch "every rupee figure"; it missed
#: "Rs." (the commonest abbreviation of the lot), lower-case "rs", "INR", the
#: rupee sign, its HTML entity, and any amount written with the unit trailing.
#: A guard that only recognises the one spelling its own tests use is not a
#: guard, and the tests confirmed the implementation rather than the property.
#:
#: The leading \b is load-bearing. Without it, and compiled with re.I, the "rs"
#: at the end of an ordinary English plural matched: "orders 4471" extracted
#: Rs 4,471.00, as did "hours 48", "customers 1200" and "numbers 88421". Nine of
#: fourteen common plurals fired. Since an extracted figure that is not in the
#: allowed set rejects the whole note, broadening the pattern had quietly
#: started rejecting correct notes -- and it did so worst on "orders", the word
#: a LEDGER_MISMATCH note is necessarily about. The failure log's own warning
#: applies: a guard that rejects everything is trivially safe and useless. The
#: reject path was widened here without re-measuring the accept path.
_SYMBOL = r"(?:\b(?:Rs|INR)\.?|₹)\s*([\d,]+(?:\.\d{1,2})?)"
_TRAILING = r"\b([\d,]+(?:\.\d{1,2})?)\s*(?:rupees?|rs\.?|inr)\b"
#: "5,000/-" -- the Indian invoice suffix. Carries no currency word at all, so
#: neither pattern above sees it.
_SUFFIX = r"\b([\d,]+(?:\.\d{1,2})?)\s*/-"
_MONEY = re.compile(f"{_SYMBOL}|{_TRAILING}|{_SUFFIX}", re.I)

#: Written before scanning, so an entity or a non-breaking space cannot smuggle
#: a figure past the pattern.
_ENTITIES = {
    "&#8377;": "₹", "&#x20b9;": "₹", "&rupee;": "₹",
    "&nbsp;": " ", "&#160;": " ", " ": " ",
}


def _normalise(text: str) -> str:
    """Fold entities and odd spaces down before the pattern ever sees them.

    This used to be a case-sensitive str.replace over a dict whose only hex
    entity was lower case, so `&#x20B9;` -- the spelling most HTML writes --
    walked straight through. The README explicitly claims entity coverage,
    which made it a documented capability that did not exist.
    """
    out = text or ""
    for k, v in _ENTITIES.items():
        out = re.sub(re.escape(k), v, out, flags=re.I)
    return out


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
    for groups in _MONEY.findall(_normalise(text)):
        raw = next((g for g in groups if g), "")
        cleaned = raw.replace(",", "")
        # "Rs ," -- an interrupted thousands separator -- used to reach int("")
        # and raise, from inside the one function whose job is to survive
        # malformed model output. The guard was the crash vector.
        if not cleaned.strip(".").strip():
            continue
        if "." not in cleaned:
            cleaned += ".00"
        whole, frac = cleaned.split(".", 1)
        if not whole:
            whole = "0"
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

    # Zero stays in. It was discarded here originally, on the assumption that
    # "Rs 0.00" is never a figure worth citing -- which is exactly backwards.
    # In a ledger mismatch or a phantom refund the difference IS zero, and that
    # the settlement ties perfectly is the single most important thing to tell
    # the analyst. Discarding it made the guard reject the truest sentence in
    # the report. Caught by the scripted-client harness; see docs/FAILURE_LOG.md.

    # Every figure the engine states in its OWN text. narrate._facts hands the
    # model `deterministic finding: <finding.explanation>`, so those numbers are
    # in the prompt: the system supplies them and then rejected the model for
    # repeating them.
    #
    # A live run found this immediately. A LEDGER_MISMATCH note quoted "a ledger
    # value of Rs 2,748.78" -- the order ledger's gross_amount, which is what
    # that entire class of finding is ABOUT, which the engine had printed itself,
    # and which was absent from this set because the set is built from settlement
    # lines and bank rows only. The model was right and the guard overruled it.
    # No stub could have found this: the scripted client cites figures from the
    # facts block or an impossible constant, never a real third-source value.
    #
    # This is the second time this function has rejected the truest sentence in
    # a note, for the second reason. The rule that covers both: a figure the
    # engine put in the prompt is by construction one the engine derived.
    for text in (finding.explanation, finding.action_required):
        for f in _figures(text or ""):
            whole, frac = f.split(".")
            vals.add(int(whole) * 100 + int(frac))
    return vals
