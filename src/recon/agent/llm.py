"""Model access, cost accounting, and the vendor boundary.

The agent layer is optional by design. The deterministic engine is the product;
this is an enhancement on top of it, and the pipeline runs to completion with no
API key and no SDK installed. That is not a convenience -- an engine that only
reconciles when a network call succeeds is not one a finance team can depend on.

THE VENDOR BOUNDARY
-------------------
Everything above this file -- narrate.py, resolve.py, guard.py -- is written
against a single method: *"here is a system instruction, a prompt and a schema;
give me back parsed JSON, and tell me what it cost."* Which company answers is a
detail that lives in this file and nowhere else.

That is what turns "the model is a replaceable part" from a claim into something
checkable: run the same reconciliation against two vendors and diff the
verdicts. If a verdict moves, the architecture was wrong.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

#: USD per million tokens.
#:
#: Anthropic's numbers are from its pricing table. Google's are from
#: ai.google.dev/gemini-api/docs/pricing, where the output column is labelled
#: "Output price (including thinking tokens)" -- see Usage.thought_tokens below
#: for why that phrase matters more than it looks.
#:
#: Only models whose prices have actually been read are listed. Guessing a price
#: would put an invented number exactly where a measured one belongs.
PRICING = {
    "claude-opus-5":    {"in": 5.00, "out": 25.00},
    "claude-sonnet-5":  {"in": 2.00, "out": 10.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
    # Gemini 3.7 Flash is promotional until 31 Dec 2026; it doubles on 1 Jan.
    "gemini-3.7-flash": {"in": 0.75, "out": 3.75},
    "gemini-3.5-flash": {"in": 1.50, "out": 9.00},
}

#: Default model per vendor.
#:
#: Opus 5 for Anthropic deliberately: the model is describing money to a human
#: who will act on the description, so trading quality for price is the
#: operator's call to make explicitly via --model, not one to bury in a
#: constant. Gemini 3.5 Flash because it is the one that has actually run here.
#:
#: gemini-3.7-flash was the default until it was tried. It is newer and half the
#: price, so on paper it is the better choice. Both models have the same free
#: allowance -- 5 a minute, 20 a day -- so the "only 3.7 can run a full batch"
#: argument that also favoured it was about a number that did not exist.
#:
#: In practice roughly eighteen requests to it timed out, starting from a fresh
#: daily quota, and a controlled retry -- same code, same key, same network, one
#: variable changed -- had 3.5 Flash answer in seconds while 3.7 hit the client
#: timeout. WHY is not established: the test that would separate "slow" from
#: "not answering" needs a longer ceiling and a quota that was already spent.
#: It stays in PRICING and available through --model, because the price was read
#: and the observation may be temporary. It is not the default, because
#: defaulting to a model this project has never had an answer from is exactly
#: the asymmetry it argues against elsewhere.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "gemini":    "gemini-3.5-flash",
}

#: Which environment variable proves each vendor is usable.
API_KEY_VARS = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "gemini":    ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

USD_TO_INR = 88.0

#: Free-tier rate limits, read off the Google AI Studio dashboard rather than
#: inferred from an error message. Both 3.5 Flash and 3.7 Flash: 5 requests per
#: minute, 20 per day, 250K tokens per minute.
#:
#: The daily figure was wrong here for a week. A 429 quoting "limit: 5" was read
#: as five requests a day, and the README, the glossary and the choice of
#: default model were all reasoned from it. It is the per-MINUTE limit. Both
#: models allow 20 a day, so a 19-call batch fits on either, and the argument
#: that only one of them could run a full batch was an argument about a number
#: that did not exist.
GEMINI_FREE_RPM = 5
GEMINI_FREE_RPD = 20

#: Seconds between Gemini calls. 60/5 = 12s is the floor; the margin covers
#: clock skew and the fact that the limit is enforced server-side over a window
#: this code cannot see.
#:
#: This was 3.5s, which is 17 requests a minute against a limit of 5. The three
#: runs that have succeeded here were 1, 2 and 3 calls, all of which fit inside
#: the allowance whatever the pacing, so nothing caught it. A 19-call batch
#: would have put seventeen requests into the first minute and collected 429s
#: from the sixth onward -- and the module would have swallowed them into
#: "0 notes accepted" and reported it as though the model had declined to help.
GEMINI_MIN_INTERVAL = float(os.environ.get("RECON_GEMINI_INTERVAL", "12.5"))

#: How many times to wait and try again when the API says "not yet".
#: A 429 is not a refusal, it is a request to slow down, and counting it as an
#: error made a throttled run indistinguishable from a broken one.
MAX_RETRIES = int(os.environ.get("RECON_LLM_RETRIES", "4"))

#: Never sleep longer than this on one retry, however long the API asks for.
#: A reconciliation run that hangs for ten minutes is its own kind of failure.
MAX_BACKOFF_S = 45.0

#: First wait when the API refuses for rate but does not say for how long. The
#: free-tier limit is per MINUTE, so a token wait of a second or two just spends
#: another request on the same refusal; this doubles from a real starting point.
RETRY_BASE_S = float(os.environ.get("RECON_LLM_RETRY_BASE", "15"))

#: Give up on a single request after this long. A reasoning model on a large
#: schema is genuinely slow -- the measured calls spent more tokens thinking
#: than answering -- so this is generous. It is not optional: with no timeout at
#: all the SDK waits forever, and a batch that produces no output until it
#: finishes then has nothing to show for the quota it spent.
GEMINI_TIMEOUT_S = float(os.environ.get("RECON_GEMINI_TIMEOUT", "90"))


@dataclass
class Usage:
    """Token and cost accounting across a run."""

    calls: int = 0
    #: Calls that came back with usable parsed JSON.
    #:
    #: `calls` counts attempts, because the tokens are spent either way and
    #: pretending otherwise would understate the bill. But the loud-failure gate
    #: used `calls` as a proxy for success, so it could only ever fire when the
    #: SDK raised. A refusal, a truncated reply or a non-completed status all
    #: recorded a call, left the gate shut, and printed a real dollar figure
    #: beside "0 notes accepted" on the way to exit 0 -- three of the four
    #: realistic failure shapes.
    successes: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    #: Calls that were rate limited and retried. Reported separately from
    #: errors: being throttled says something about the plan, being refused
    #: says something about the request, and one must not read as the other.
    throttled: int = 0
    #: Calls the circuit breaker never made. Counted apart from `errors`,
    #: because a call that was never attempted is not a model failure and must
    #: not be reported as one -- nineteen skipped calls printed as "api errors
    #: 19" is indistinguishable from nineteen refusals, and the exit-3 message
    #: then stated a false fact about the run.
    skipped: int = 0
    #: Why the run stopped early, printed unconditionally when set.
    gave_up: str = ""
    #: Reasoning tokens the model generated but did not return.
    #:
    #: Invisible in the reply and absent from output_tokens, but billed at the
    #: output rate. The first real Gemini call made here returned 61 in, 60 out
    #: and 275 thought -- so counting only output_tokens would have understated
    #: the run roughly fivefold, and the wrong figure would have looked entirely
    #: reasonable sitting in the report. Counted separately, billed as output.
    thought_tokens: int = 0
    errors: int = 0
    model: str = ""

    def record(self, *, inp: int = 0, out: int = 0, cached: int = 0,
               thought: int = 0):
        """One successful call. Backends report their own numbers."""
        self.calls += 1
        self.input_tokens += inp or 0
        self.output_tokens += out or 0
        self.cache_read_tokens += cached or 0
        self.thought_tokens += thought or 0

    @property
    def billable_output(self) -> int:
        return self.output_tokens + self.thought_tokens

    @property
    def usd(self) -> float:
        p = PRICING.get(self.model)
        if p is None:
            # An unknown model has no price. Returning 0.0 would print "$0.0000"
            # next to real token counts, which reads as "free" rather than as
            # "unknown" -- so the caller checks for None and prints neither.
            return None
        return (self.input_tokens / 1e6 * p["in"]
                + self.billable_output / 1e6 * p["out"])

    def per_n_records(self, n: int, per: int = 100, complete: bool = True) -> dict:
        """Cost scaled to `per` records -- only when the batch was fully processed.

        A capped run (--narrate-limit) pays for a few notes and this used to
        divide that cost across every settlement, as though the whole batch had
        been done: two notes out of nineteen reported $0.0199 per 100 records,
        a genuine measurement wearing a label that did not describe it, and
        wrong in the flattering direction, which is the worst way to be wrong
        about cost.

        What the full-batch figure would have been is NOT recorded here, and the
        first draft of this docstring got that wrong too -- it named $0.1895 as
        "the real figure" when $0.1895 is just $0.0199 x 19/2, the same
        extrapolation this method exists to refuse, done by hand. Two calls is a
        sample. The honest statement is that the run was capped and no per-batch
        cost follows from it.
        """
        if not n or self.usd is None or not complete or not self.successes:
            return {}
        # `complete` only says the OPERATOR did not cap the run. A batch that
        # lost calls to errors, or skipped them when the breaker tripped, did
        # not narrate every exception either -- and scaling what it did spend
        # across all the records reports a batch cheaper than a real one. An
        # offline dry run of 19 calls with one timeout reported $0.1712 per 100
        # records for 18 notes' worth of work. Understated, in the flattering
        # direction, which is the same shape as the capped-run bug above.
        if self.errors or self.skipped:
            return {}
        scale = per / n
        return {
            "usd": round(self.usd * scale, 6),
            "inr": round(self.usd * USD_TO_INR * scale, 4),
            "input_tokens": round(self.input_tokens * scale),
            "output_tokens": round(self.billable_output * scale),
        }

    def to_dict(self, n_records: int = 0, complete: bool = True) -> dict:
        usd = self.usd
        return {
            # Reports what actually happened, not what was requested. A run
            # that lost calls did not complete, whatever the operator asked for.
            "batch_complete": bool(complete and not self.errors
                                   and not self.skipped),
            "uncapped_by_operator": complete,
            # Per NOTE, so the denominator is notes -- calls that failed still
            # cost money but produced nothing, and dividing by attempts reported
            # 19 calls with 1 usable note as if each note cost a nineteenth of
            # what it did.
            "usd_per_note": None if usd is None or not self.successes
                            else round(usd / self.successes, 6),
            "usd_per_call": None if usd is None or not self.calls
                            else round(usd / self.calls, 6),
            "model": self.model,
            "calls": self.calls,
            "successes": self.successes,
            "errors": self.errors,
            "skipped": self.skipped,
            "gave_up": self.gave_up,
            "throttled": self.throttled,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thought_tokens": self.thought_tokens,
            "billable_output_tokens": self.billable_output,
            "cache_read_tokens": self.cache_read_tokens,
            "price_known": usd is not None,
            "usd_total": None if usd is None else round(usd, 6),
            "inr_total": None if usd is None else round(usd * USD_TO_INR, 4),
            "per_100_records": self.per_n_records(n_records, complete=complete),
        }


class LLMUnavailable(RuntimeError):
    """Raised when the agent layer is requested but cannot run."""


# --------------------------------------------------------------------------
# Backends -- one per vendor, one method each
# --------------------------------------------------------------------------

def _retry_after(exc, attempt: int = 0) -> float | None:
    """Seconds to wait, if this exception is a rate limit. None if it is not.

    The API states its own delay ("Please retry in 23.85s"), which is better
    information than any backoff schedule we could invent, so it is used when
    present and a doubling fallback when it is not.
    """
    text = f"{type(exc).__name__} {exc}"
    if "429" not in text and "RateLimit" not in text and "too_many_requests" not in text:
        return None
    m = re.search(r"retry in ([\d.]+)s", text)
    if m:
        return min(float(m.group(1)) + 0.5, MAX_BACKOFF_S)
    # The fallback this docstring has always promised, and did not have: it
    # returned None here, so a 429 that did not happen to phrase its delay was
    # treated as a hard error -- no wait, no retry, the call burned and counted
    # against a quota it had already been refused by. Not one of the runs so far
    # was long enough to hit a per-minute limit, so nothing exercised it.
    return min(RETRY_BASE_S * (2 ** attempt), MAX_BACKOFF_S)
    return None


def _debug(exc):
    """Show a swallowed error when asked.

    Errors are swallowed on purpose so a bad afternoon at some API cannot stop
    the books closing -- but "49 errors" with no way to see even one of them is
    not diagnosable. Set RECON_LLM_DEBUG=1 to print them.
    """
    if os.environ.get("RECON_LLM_DEBUG"):
        import sys
        import traceback
        print(f"[llm] {type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("RECON_LLM_DEBUG") == "2":
            traceback.print_exc()


#: Sentinel meaning "the API asked us to wait" -- distinct from None, which
#: means the call genuinely failed and the deterministic text stands.
_RETRY = object()


class Backend:
    """A vendor behind one method.

    Subclasses return a parsed dict, or None if anything at all went wrong.
    They never raise: a failed narration call must degrade to the deterministic
    explanation, never take down a reconciliation run. The books still have to
    close if somebody's API is having a bad afternoon.
    """

    provider = "?"

    def complete(self, model, system, prompt, schema, max_tokens, usage):
        raise NotImplementedError


class AnthropicBackend(Backend):
    provider = "anthropic"

    def __init__(self, client):
        self.client = client

    def complete(self, model, system, prompt, schema, max_tokens, usage):
        try:
            resp = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema",
                                          "schema": schema}},
            )
            u = getattr(resp, "usage", None)
            usage.record(
                inp=getattr(u, "input_tokens", 0) if u else 0,
                out=getattr(u, "output_tokens", 0) if u else 0,
                cached=getattr(u, "cache_read_input_tokens", 0) if u else 0,
            )
            stop = getattr(resp, "stop_reason", None)
            if stop in ("refusal", "max_tokens"):
                usage.errors += 1
                _debug(RuntimeError(f"stop_reason={stop}"))
                return None
            text = next((b.text for b in resp.content if b.type == "text"), None)
            return json.loads(text) if text else None
        except Exception as e:
            usage.errors += 1
            _debug(e)
            return None


class GeminiBackend(Backend):
    provider = "gemini"

    def __init__(self, client):
        self.client = client
        self._last_call = 0.0
        #: Consecutive calls that exhausted every retry and were still rate
        #: limited. Once the quota is gone it stays gone, and continuing to ask
        #: only converts a fast failure into a slow one.
        self._exhausted = 0
        self._gave_up = False

    def complete(self, model, system, prompt, schema, max_tokens, usage):
        # Circuit breaker. Retrying is right for a transient limit and wrong for
        # an exhausted daily quota, and the two look identical from here. With
        # 4 retries sleeping up to 45s each, a 19-call run against a spent quota
        # took the better part of an hour to report that it had done nothing --
        # silently, because the errors are swallowed by design. Two calls that
        # burn all their retries is enough evidence to stop asking.
        if self._gave_up:
            usage.skipped += 1
            return None

        for attempt in range(MAX_RETRIES + 1):
            out = self._attempt(model, system, prompt, schema, usage,
                                max_tokens, attempt=attempt,
                                last=attempt == MAX_RETRIES)
            if out is not _RETRY:
                if out is not None:
                    self._exhausted = 0
                return out

        self._exhausted += 1
        if self._exhausted >= 2:
            self._gave_up = True
            # Not via _debug(). This is the reason the rest of the batch is
            # missing, and hiding it behind RECON_LLM_DEBUG made a stopped run
            # look like a refused one.
            usage.gave_up = (
                f"stopped after {self._exhausted} consecutive calls exhausted "
                f"every retry; the remaining calls were not attempted. The "
                f"quota is most likely spent for the day.")
        return None

    def _attempt(self, model, system, prompt, schema, usage, max_tokens,
                 attempt=0, last=False):
        # Free-tier pacing, so most calls never hit the limit in the first
        # place. The retry above is the safety net, not the strategy.
        wait = GEMINI_MIN_INTERVAL - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

        try:
            r = self.client.interactions.create(
                model=model,
                input=prompt,
                system_instruction=system,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
                # No output cap is sent. `max_tokens` is accepted by complete()
                # for one signature across both vendors and is genuinely unused
                # here, which a review correctly flagged.
                #
                # The fix for that flag was worse than the flag. Passing
                # max_output_tokens= raises TypeError inside the SDK before any
                # request goes out ("Use extra_body=... to send additional
                # request body fields"), so a 19-call batch produced 19 errors,
                # zero calls and zero tokens -- a total outage of the live path,
                # introduced to close a nice-to-have. It shipped because the
                # only test of this backend is a stub built to the Anthropic
                # wire shape, which cannot see a vendor signature.
                #
                # extra_body={"max_output_tokens": ...} passes the SDK, but
                # whether the API accepts that body field is unverified, and
                # spending a whole day's free quota to find out is how the last
                # guess went. So: no cap, stated rather than hidden. Gemini's
                # own default applies. test_gemini_call_uses_arguments_the_sdk
                # _accepts now checks this call against the installed SDK.
                #
                # In SECONDS. The SDK documents this parameter as "the request
                # timeout for this method in seconds" and multiplies it by 1000
                # internally; HttpOptions.timeout, by contrast, is milliseconds
                # and only reaches the async client. Without a timeout here a
                # stalled call blocks forever: one batch sat for over half an
                # hour inside _receive_response_headers, having sent a request
                # that never got an answer, and was killed with nothing written.
                timeout=GEMINI_TIMEOUT_S,
            )
            u = getattr(r, "usage", None)
            if u:
                usage.record(
                    inp=u.total_input_tokens,
                    out=u.total_output_tokens,
                    cached=u.total_cached_tokens,
                    thought=u.total_thought_tokens,
                )
            else:
                usage.record()

            if r.status != "completed":
                usage.errors += 1
                return None
            return json.loads(r.output_text) if r.output_text else None
        except Exception as e:
            delay = _retry_after(e, attempt)
            if delay is not None and last:
                # Out of retries and still limited: let complete() count it.
                usage.errors += 1
                _debug(e)
                return _RETRY
            if delay is not None:
                # Told to wait, not told no. Waiting is the correct response.
                usage.throttled += 1
                _debug(e)
                time.sleep(delay)
                return _RETRY
            usage.errors += 1
            _debug(e)
            return None


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------

def resolve_provider(requested: str | None) -> str:
    """Which vendor to use. 'auto' picks whichever key is present."""
    if requested and requested != "auto":
        return requested
    for name, keys in API_KEY_VARS.items():
        if any(os.environ.get(k) for k in keys):
            return name
    raise LLMUnavailable(
        "no model API key in the environment. Set ANTHROPIC_API_KEY or "
        "GEMINI_API_KEY. The agent layer is optional -- the deterministic "
        "engine is the product and runs without it. To exercise the agent "
        "code path offline instead, use --llm-stub.")


def build_client(provider: str | None = None) -> Backend:
    """Return a Backend, or raise LLMUnavailable with a reason a human can act on."""
    provider = resolve_provider(provider)

    # Neither SDK raises when there is no key: both store it as None and fail
    # per request, which complete() swallows into None by design. The result was
    # a run printing a real model id, zero tokens, "$0.0000 per 100 records" and
    # exit 0 -- a fabricated cost figure sitting exactly where a measured one
    # goes. Checked here so the failure is loud and early.
    keys = API_KEY_VARS[provider]
    if not any(os.environ.get(k) for k in keys):
        raise LLMUnavailable(
            f"--provider {provider} was requested but none of "
            f"{' / '.join(keys)} is set in the environment.")

    if provider == "anthropic":
        try:
            import anthropic
        except ImportError as e:
            raise LLMUnavailable(
                "the 'anthropic' package is not installed. "
                "Install it with:  pip install -e '.[agent]'") from e
        try:
            return AnthropicBackend(anthropic.Anthropic())
        except Exception as e:
            raise LLMUnavailable(
                f"could not construct an Anthropic client ({e}).") from e

    if provider == "gemini":
        try:
            from google import genai
        except ImportError as e:
            raise LLMUnavailable(
                "the 'google-genai' package is not installed. "
                "Install it with:  pip install -e '.[gemini]'") from e
        try:
            # The timeout is set per request, at the call site, NOT here.
            # HttpOptions(timeout=...) reaches only the async client; the
            # synchronous path this project uses goes through a different SDK
            # layer and ignores it. Setting it here looked like a fix and was
            # inert -- the same shape of mistake as accepting max_tokens and
            # never sending it. See GeminiBackend._attempt.
            return GeminiBackend(genai.Client())
        except Exception as e:
            raise LLMUnavailable(
                f"could not construct a Gemini client ({e}).") from e

    raise LLMUnavailable(f"unknown provider {provider!r}")


def structured_call(backend, model: str, system: str, prompt: str,
                    schema: dict, usage: Usage, max_tokens: int = 4096):
    """One structured-output call. Returns a parsed dict, or None on failure.

    This function no longer knows which vendor it is talking to, and that is the
    entire point of it.

    Success is counted here, in one place, rather than in each backend: a call
    succeeded exactly when it produced something the caller can use.

    max_tokens was 1024, which has to hold an exception note AND whatever
    reasoning the model does first. Thinking is on by default on the Anthropic
    default model, and the measured Gemini run spent 87% of its output tokens on
    it -- so a truncated reply is a plausible shape of failure on a path that
    has never been exercised against a live key.

    It applies on Anthropic only. GeminiBackend accepts the argument and does
    not send it, because `interactions.create()` has no output-cap parameter and
    passing one raises TypeError inside the SDK before any request is made. See
    the comment at that call site: trying to wire it through took the live path
    from working to nineteen errors and zero calls, which is a considerably
    worse outcome than the unused argument it was fixing.

    On Anthropic the ceiling is shared with thinking tokens, so 4096 may still
    be too low; --model and an explicit cap are the operator's lever.
    """
    out = backend.complete(model, system, prompt, schema, max_tokens, usage)
    if out is not None:
        usage.successes += 1
    return out
