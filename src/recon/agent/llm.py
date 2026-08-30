"""Anthropic client wiring, model config, and cost accounting.

The agent layer is optional by design. The deterministic engine is the product;
this is an enhancement on top of it, and the pipeline runs to completion with
`--no-llm` (the default) on a machine with no API key and no `anthropic`
package installed. That is not a convenience -- an engine that only reconciles
when a network call succeeds is not one a finance team can depend on.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

#: USD per million tokens, from the Anthropic pricing table.
PRICING = {
    "claude-opus-5":   {"in": 5.00, "out": 25.00},
    "claude-sonnet-5": {"in": 2.00, "out": 10.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
}

#: Default model. Opus 5 is the default deliberately: the model is being asked
#: to describe money to a human who will act on the description, and choosing a
#: cheaper model is a cost/quality tradeoff for the operator to make explicitly
#: via --model, not one to bury in a constant. Measured cost per 100 records is
#: reported either way.
DEFAULT_MODEL = "claude-opus-5"

USD_TO_INR = 88.0


@dataclass
class Usage:
    """Token and cost accounting across a run."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    errors: int = 0
    model: str = DEFAULT_MODEL

    def add(self, resp):
        self.calls += 1
        u = getattr(resp, "usage", None)
        if u is None:
            return
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0

    @property
    def usd(self) -> float:
        p = PRICING.get(self.model, PRICING[DEFAULT_MODEL])
        return (self.input_tokens / 1e6 * p["in"]
                + self.output_tokens / 1e6 * p["out"])

    def per_n_records(self, n: int, per: int = 100) -> dict:
        if not n:
            return {}
        scale = per / n
        return {
            "usd": round(self.usd * scale, 6),
            "inr": round(self.usd * USD_TO_INR * scale, 4),
            "input_tokens": round(self.input_tokens * scale),
            "output_tokens": round(self.output_tokens * scale),
        }

    def to_dict(self, n_records: int = 0) -> dict:
        return {
            "model": self.model,
            "calls": self.calls,
            "errors": self.errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "usd_total": round(self.usd, 6),
            "inr_total": round(self.usd * USD_TO_INR, 4),
            "per_100_records": self.per_n_records(n_records),
        }


class LLMUnavailable(RuntimeError):
    """Raised when the agent layer is requested but cannot run."""


def build_client():
    """Return an Anthropic client, or raise LLMUnavailable with a clear reason."""
    try:
        import anthropic
    except ImportError as e:
        raise LLMUnavailable(
            "the 'anthropic' package is not installed. "
            "Install it with:  pip install -e '.[agent]'") from e

    # The SDK does NOT raise when there is no key: it stores api_key=None and
    # fails per-request, which structured_call swallows into None by design. The
    # result was a run that printed a real model id, zero tokens, "$0.0000 per
    # 100 records" and exited 0 -- a fabricated cost figure sitting exactly
    # where a measured one goes. Checked here so the failure is loud and early.
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise LLMUnavailable(
            "no ANTHROPIC_API_KEY in the environment. The agent layer is "
            "optional; the deterministic engine is the product and runs "
            "without it. To exercise the agent code path offline instead, "
            "use --llm-stub.")

    try:
        return anthropic.Anthropic()
    except Exception as e:
        raise LLMUnavailable(
            f"could not construct an Anthropic client ({e}). Set ANTHROPIC_API_KEY "
            "or run 'ant auth login'.") from e


def structured_call(client, model: str, system: str, prompt: str,
                    schema: dict, usage: Usage, max_tokens: int = 1024):
    """One structured-output call. Returns a parsed dict, or None on failure.

    Errors are swallowed into None on purpose. A failed narration call must
    degrade to the deterministic explanation, never take down a reconciliation
    run -- the books still have to close if the API is having a bad afternoon.
    """
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        usage.add(resp)
        if getattr(resp, "stop_reason", None) == "refusal":
            usage.errors += 1
            return None
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return json.loads(text) if text else None
    except Exception:
        usage.errors += 1
        return None
