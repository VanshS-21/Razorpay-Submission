"""A scripted stand-in for the Anthropic client, for testing the guard.

**This is not a simulation of model quality and no number it produces is a
measurement of anything.** It exists for one reason: the guard is the part of
this system I wrote, and it needs to be exercised end to end -- including its
failure paths -- without an API key.

Testing "the guard rejects invented figures" by hoping a real model eventually
invents one would be untestable. Scripting a model that definitely misbehaves
makes the safety property a deterministic, repeatable assertion.

Scenarios:

    honest        well-formed notes using only figures from the facts
    hallucinating a note containing a figure the engine never computed
    overreaching  a match proposal that fails the arithmetic re-check
    failing       raises, to prove a dead API degrades instead of exploding
    refusing      returns stop_reason="refusal" WITH usable-looking content
    truncated     valid-looking partial JSON with stop_reason="max_tokens"

Every scenario except `honest` must be caught. That is the test.

`refusing` used to return empty text alongside the refusal, so
`structured_call` returned None on the `if text else None` line before the
stop_reason guard was ever consulted -- deleting that guard entirely left all
135 tests green. The test named for it passed through a path it did not test.
This is the third instance of the mistake docs/FAILURE_LOG.md already records
twice: a test over a path that cannot execute is not weak evidence, it is no
evidence. Both scenarios now return content that WOULD parse, so the guard is
the only thing that can reject them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

SCENARIOS = ("honest", "hallucinating", "overreaching", "failing",
             "refusing", "truncated", "plausible")

#: A figure provably outside any allowed set this engine can produce.
#: The generator caps a single line at 15,000,000 paise (Rs 1.5 lakh) and a
#: settlement at 14 lines, so no computed value can exceed ~210,000,000 paise.
#: This is 999,999,999 paise (Rs 1 crore), which therefore cannot collide with a
#: real figure -- making the hallucination test deterministic rather than likely.
IMPOSSIBLE_FIGURE = "Rs 99,99,999.99"


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list
    usage: _Usage
    stop_reason: str = "end_turn"


class _Messages:
    def __init__(self, scenario: str):
        self.scenario = scenario
        self.calls = 0

    def create(self, *, model, max_tokens, system, messages, output_config=None,
               **kw):
        self.calls += 1
        prompt = messages[0]["content"]

        if self.scenario == "failing":
            raise RuntimeError("simulated API failure")

        usage = _Usage(input_tokens=len(system) // 4 + len(prompt) // 4,
                       output_tokens=60)

        if self.scenario == "refusing":
            # Content that would parse cleanly. Only the stop_reason says no.
            return _Response(
                [_Block(json.dumps({
                    "explanation": "I cannot help with that request.",
                    "action_required": "n/a"}))],
                usage, stop_reason="refusal")

        if self.scenario == "truncated":
            # A reply that ran out of budget mid-object. Valid-looking right up
            # to the point where it stops, which is exactly why the stop_reason
            # and not the parser has to catch it.
            body = json.dumps({
                "explanation": "The payout and the bank credit disagree by",
                "action_required": "Compare the PSP payout advice against"})
            return _Response([_Block(body[:-12])], usage,
                             stop_reason="max_tokens")

        is_match = "Which candidate does this row refer to?" in prompt
        if is_match:
            payload = self._match(prompt)
        else:
            payload = self._note(prompt)

        return _Response([_Block(json.dumps(payload))], usage)

    # -- payloads ---------------------------------------------------------

    def _note(self, prompt: str) -> dict:
        # Quote a figure that genuinely appears in the facts.
        real = re.search(r"difference: (Rs [\d,]+\.\d{2})", prompt)
        figure = real.group(1) if real else "Rs 0.00"

        if self.scenario == "hallucinating":
            figure = IMPOSSIBLE_FIGURE

        return {
            "explanation": (
                f"The payout and the bank credit disagree by {figure}. "
                f"No refund, chargeback or itemised charge in any of the three "
                f"sources accounts for the difference."),
            "action_required": (
                "Compare the PSP payout advice against the bank statement line "
                "for this UTR and identify the difference before closing."),
        }

    def _match(self, prompt: str) -> dict:
        ids = re.findall(r"- (setl_\w+) \|", prompt)
        if not ids:
            return {"settlement_id": None, "evidence": "no candidates",
                    "confidence": 0.0}

        if self.scenario == "overreaching":
            # Confidently name a candidate whose amount cannot possibly tie:
            # pick the LAST one regardless of the figures. The guard must catch
            # this on arithmetic, not on the model's stated confidence.
            return {"settlement_id": ids[-1],
                    "evidence": "the narration mentions Razorpay",
                    "confidence": 0.99}

        # "plausible" exists because every other scenario is caught by
        # arithmetic, which meant the ACCEPT path was never exercised and the
        # invariant test could not fail. This one names the first candidate --
        # on an ambiguous row, that candidate ties on amount and date by
        # definition, so the guard has nothing left to reject it with. It is
        # the shape of proposal that used to clear a payout on the model's say
        # so. It must now change nothing at all.
        return {"settlement_id": ids[0],
                "evidence": "merchant name and truncated reference fragment",
                "confidence": 0.8}


class ScriptedClient:
    """Mimics the surface of `anthropic.Anthropic` used by this project."""

    def __init__(self, scenario: str = "honest"):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; "
                             f"expected one of {SCENARIOS}")
        self.scenario = scenario
        self.messages = _Messages(scenario)
