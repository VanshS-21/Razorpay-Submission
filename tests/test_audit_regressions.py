"""Regression guards for the findings of the third independent audit.

Every test here pins a property that was BROKEN and has been fixed, or that a
mutation survived because nothing observed it. Three audits have now found the
same shape of problem: a claim in the documentation that no test could tell had
stopped being true. So the rule applied here is the one docs/FAILURE_LOG.md
states and this project has failed three times -- a test over a path that cannot
execute is not weak evidence, it is no evidence. Each test below is written to
fail if its fix is reverted, and each was confirmed to do so.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from unittest import mock
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recon.adversarial import write_holdout                    # noqa: E402
from recon.agent.guard import _figures                         # noqa: E402
from recon.agent import llm                                    # noqa: E402
from recon.agent.llm import Usage                              # noqa: E402
from recon.engine.matcher import match                         # noqa: E402
from recon.engine.pipeline import run                          # noqa: E402
from recon.generate import write_dataset                       # noqa: E402
from recon.ingest import IngestError, read_settlement_lines    # noqa: E402
from recon.models import (                                     # noqa: E402
    AnomalyClass,
    BankCredit,
    Disposition,
    EntityType,
    ReconUnit,
    SettlementLine,
    expected_disposition,
)
from recon.report import load_truth, render_console, score     # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _dataset(tmp_path, seed=42, n=40):
    write_dataset(tmp_path, seed=seed, n_settlements=n)
    return tmp_path


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _first_clean(findings):
    for f in findings:
        if f.reason_code is AnomalyClass.CLEAN:
            return f.settlement_id
    raise AssertionError("dataset has no CLEAN settlement to attack")


def _inject_debit(d: Path, sid: str, kind: str, amount: int, order_id: str):
    """Add a money-moving line to `sid` and shrink its bank credit to match.

    The settlement then still ties to the bank exactly -- which is the whole
    point. Only the order ledger can see that something is wrong.
    """
    rows = _rows(d / "settlement_recon.csv")
    src = next(r for r in rows if r["settlement_id"] == sid)
    ghost = dict.fromkeys(src, "")
    ghost.update({
        "entity_id": f"{kind}_GHOST", "type": kind, "debit": str(amount),
        "credit": "0", "amount": str(amount), "currency": "INR", "fee": "0",
        "tax": "0", "settlement_id": sid,
        "settlement_utr": src["settlement_utr"],
        "created_at": src["created_at"], "settled_at": src["settled_at"],
        "payment_id": "", "order_id": order_id, "method": "upi",
        "description": f"{kind} processed",
    })
    rows.append(ghost)
    _write(d / "settlement_recon.csv", rows)

    bank = _rows(d / "bank_statement.csv")
    hit = next(r for r in bank if r["ref_no"] == src["settlement_utr"])
    hit["credit"] = str(int(hit["credit"]) - amount)
    _write(d / "bank_statement.csv", bank)


# --------------------------------------------------------------------------
# C1 / C2 -- money moving against an order the books do not have
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind,order_id", [
    # C1: a refund with the order_id column simply left empty walked past
    # unsupported_refunds, because that rule skipped any refund without one.
    ("refund", ""),
    # C1b: a refund naming an order the ledger has never heard of.
    ("refund", "order_DOES_NOT_EXIST"),
    # A payment line inventing a sale. Caught only if it named a real order.
    ("payment", ""),
    ("payment", "order_DOES_NOT_EXIST"),
    # C2: ADJUSTMENT was in the taxonomy with no rule anywhere inspecting one,
    # so a line of any size flowed into expected_net unchecked.
    ("adjustment", ""),
    ("dispute", "order_DOES_NOT_EXIST"),
])
def test_a_line_with_no_order_behind_it_never_clears(tmp_path, kind, order_id):
    d = _dataset(tmp_path)
    findings, *_ = run(d)
    sid = _first_clean(findings)

    _inject_debit(d, sid, kind, 500_000, order_id)
    findings, *_ = run(d)
    f = next(x for x in findings if x.settlement_id == sid)

    assert f.disposition is Disposition.EXCEPTION, (
        f"Rs 5,000 moved on a {kind} line the ledger cannot corroborate "
        f"(order_id={order_id!r}) and the settlement still cleared as "
        f"{f.reason_code.value}. It ties to the bank exactly, so no "
        f"totals-based check can ever see it.")
    assert f.reason_code is AnomalyClass.LEDGER_MISMATCH


def test_the_headline_defence_is_not_bypassed_by_an_empty_column(tmp_path):
    """The engine must not report a clean bill of health over this."""
    d = _dataset(tmp_path)
    findings, *_ = run(d)
    _inject_debit(d, _first_clean(findings), "refund", 500_000, "")
    findings, *_ = run(d)
    metrics = score(findings, load_truth(d))
    # The answer key calls the doctored settlement clean, so the scored rate
    # cannot see this at all -- which is exactly why the disposition is asserted
    # directly rather than through the metrics.
    assert any(f.reason_code is AnomalyClass.LEDGER_MISMATCH
               and f.disposition is Disposition.EXCEPTION
               for f in findings)


# --------------------------------------------------------------------------
# C3 -- units decided per file, not per cell
# --------------------------------------------------------------------------

def test_a_rupee_export_is_not_read_at_one_hundredth_of_its_value(tmp_path):
    """`fee` 16.95 on one row and 17 on the next used to differ 100-fold.

    The units decision was made per CELL: a decimal point meant rupees, its
    absence meant paise. A whole-rupee cell in a rupee file was therefore read
    as paise, silently -- and because it scaled every column of the row by the
    same factor, the settlement stayed internally consistent, tied to the bank,
    and reconciled CLEAN at 1% of its true value.
    """
    paise = _dataset(tmp_path / "paise")
    rupees = tmp_path / "rupees"
    rupees.mkdir()

    cols = {"settlement_recon.csv": ("debit", "credit", "amount", "fee", "tax"),
            "bank_statement.csv": ("debit", "credit"),
            "order_ledger.csv": ("gross_amount",)}
    bare = 0
    for name, money in cols.items():
        rows = _rows(paise / name)
        for r in rows:
            for c in money:
                p = int(r[c] or 0)
                # Whole rupees written bare -- the shape that used to break.
                r[c] = str(p // 100) if p % 100 == 0 else f"{p / 100:.2f}"
                bare += p % 100 == 0
        _write(rupees / name, rows)
    (rupees / "ground_truth.json").write_text(
        (paise / "ground_truth.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    assert bare > 100, "fixture must contain bare-integer money cells"

    a, *_ = run(paise)
    b, *_ = run(rupees)
    assert [(f.settlement_id, f.reason_code, f.delta) for f in a] == \
           [(f.settlement_id, f.reason_code, f.delta) for f in b], (
        "the same books written in rupees reconcile differently from the same "
        "books written in paise")


# --------------------------------------------------------------------------
# M2 / N3 -- shapes a real export actually contains
# --------------------------------------------------------------------------

def test_a_utf8_bom_does_not_hide_the_first_column(tmp_path):
    """Excel's "CSV UTF-8" writes one, and it becomes part of the first name.

    The reader then reported `entity_id` missing and listed `entity_id` among
    the columns it had found, because the escape renders as the bare name.
    """
    d = _dataset(tmp_path)
    for name in ("settlement_recon.csv", "bank_statement.csv",
                 "order_ledger.csv"):
        p = d / name
        p.write_text(p.read_text(encoding="utf-8"), encoding="utf-8-sig")
    findings, *_ = run(d)
    assert findings


def test_one_trailing_comma_does_not_kill_the_file(tmp_path):
    d = _dataset(tmp_path)
    p = d / "settlement_recon.csv"
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[3] += ","
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert read_settlement_lines(p)


def test_an_extra_field_carrying_data_still_raises(tmp_path):
    """The arity check has to keep working -- a truncated export is real."""
    d = _dataset(tmp_path)
    p = d / "settlement_recon.csv"
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[3] += ",surprise"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(IngestError):
        read_settlement_lines(p)


def test_a_short_row_still_raises(tmp_path):
    d = _dataset(tmp_path)
    p = d / "settlement_recon.csv"
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[3] = ",".join(lines[3].split(",")[:-3])
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(IngestError):
        read_settlement_lines(p)


def test_a_blank_amount_is_refused_rather_than_read_as_zero(tmp_path):
    """A wrong zero balances, so it is invisible in every total."""
    d = _dataset(tmp_path)
    p = d / "settlement_recon.csv"
    rows = _rows(p)
    rows[0]["amount"] = ""
    _write(p, rows)
    with pytest.raises(IngestError, match="empty"):
        read_settlement_lines(p)


def test_a_blank_fee_on_a_refund_row_is_accepted(tmp_path):
    """Refusing was rolled out per FILE, so real exports stopped loading."""
    d = _dataset(tmp_path)
    p = d / "settlement_recon.csv"
    rows = _rows(p)
    rows[0]["fee"] = ""
    rows[0]["tax"] = ""
    _write(p, rows)
    assert read_settlement_lines(p)


def test_a_currency_that_is_not_inr_is_refused(tmp_path):
    """`currency` was read from all three sources and compared nowhere."""
    d = _dataset(tmp_path)
    p = d / "settlement_recon.csv"
    rows = _rows(p)
    rows[0]["currency"] = "USD"
    _write(p, rows)
    with pytest.raises(IngestError, match="USD"):
        read_settlement_lines(p)


# --------------------------------------------------------------------------
# M1 / R1 -- the guard must reject invented money, not ordinary English
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "Cross-check orders 4471 and 4472 against the invoice.",
    "The bank posted the credit 24 hours 48 minutes late.",
    "Compare the reference numbers 88421 on both sides.",
    "Contact customers 1200 and 1201 about the refund.",
    "There were errors 3 lines deep in the export.",
    "Two transfers 900 apart in the ledger.",
])
def test_an_english_plural_before_a_number_is_not_money(sentence):
    """Broadening the pattern to catch "INR" also caught "orde-rs 4471".

    Compiled with re.I and no left word boundary, any word ending in "rs"
    became a currency symbol. An extracted figure absent from the allowed set
    rejects the whole note, so the guard had started rejecting correct notes --
    worst on "orders", the word a LEDGER_MISMATCH note is necessarily about.
    """
    assert _figures(sentence) == set(), (
        f"guard read a rupee figure out of ordinary English: {sentence!r}")


@pytest.mark.parametrize("spelling", [
    "Rs 1,234.56", "Rs. 1,234.56", "Rs1234.56", "RS 1234.56", "rs 1234.56",
    "INR 1,234.56", "INR. 1234.56", "inr 1234.56", "INR1234.56",
    "₹ 1,234.56", "₹1234.56", "1,234.56 rupees", "1234.56/-",
])
def test_every_spelling_of_a_rupee_figure_is_still_caught(spelling):
    """The accept path was widened without re-measuring the reject path once.

    It must not happen in the other direction either: narrowing the pattern to
    fix the false positives above must not let a real figure through.
    """
    assert _figures(spelling), f"guard stopped seeing money in {spelling!r}"


# --------------------------------------------------------------------------
# M8 -- a contested subset-sum is not resolved by CSV row order
# --------------------------------------------------------------------------

def _unit(sid, utr, net):
    line = SettlementLine(
        entity_id=f"pay_{sid}", type=EntityType.PAYMENT, debit=0, credit=net,
        amount=net, currency="INR", fee=0, tax=0, settlement_id=sid,
        settlement_utr=utr, created_at="2026-06-01", settled_at="2026-06-01",
        payment_id=None, order_id=f"order_{sid}", method="upi", description="")
    return ReconUnit(settlement_id=sid, utr=utr, lines=[line])


@pytest.mark.parametrize("order", [
    ("A", "B", "C"), ("C", "B", "A"), ("B", "A", "C"), ("A", "C", "B")])
def test_three_anchors_competing_for_one_payout_all_escalate(order):
    """Whoever appeared first in the CSV used to be cleared; the rest escalated.

    `subset_summing_to` already refuses when more than one subset fits a target,
    on the grounds that clearing on that basis is a guess wearing the costume of
    a proof. It cannot see this case: it reasons about one target at a time, and
    each answer is locally unique. The same principle has to apply across
    anchors, or the verdict is decided by row order.
    """
    spare_net, net = 100_000, 500_000
    units, bank = {}, []
    for sid in order:
        units[sid] = _unit(sid, f"utr{sid}", net)
        bank.append(BankCredit(
            txn_id=f"b{sid}", value_date="2026-06-01",
            narration=f"NEFT-utr{sid}", ref_no=f"utr{sid}",
            debit=0, credit=net + spare_net))
    units["SPARE"] = _unit("SPARE", "utrSPARE", spare_net)

    res = match(units, bank)
    assert not any(res.group.get(s) for s in units), (
        "one anchor was cleared on a coin flip: three surpluses are "
        "arithmetically indistinguishable and only one spare payout exists")
    assert "SPARE" in res.unpaid


# --------------------------------------------------------------------------
# R3 -- the answer key must not accept the mistake as a second reading
# --------------------------------------------------------------------------

def test_the_holdout_key_never_accepts_a_reconciled_code_for_an_exception(tmp_path):
    """`also_acceptable` listed `refund_netted_later` on the phantom refunds.

    That is a RECONCILED-class code on a settlement that must escalate, and it
    is exactly the code the Day 2 bug emitted when it cleared those four. While
    it was accepted, holdout reason-code accuracy read 100% straight through
    this project's worst regression.
    """
    write_holdout(tmp_path, seed=1337)
    truth = json.loads((tmp_path / "ground_truth.json").read_text(encoding="utf-8"))
    rows = truth if isinstance(truth, list) else list(truth.values())
    assert rows
    for r in rows:
        want = Disposition(r["expected_disposition"])
        for code in r["also_acceptable"]:
            cls = AnomalyClass(code)          # a dead string must not survive
            assert expected_disposition(cls) is want, (
                f"{r['settlement_id']}: {code} would have been graded correct, "
                f"but it is a {expected_disposition(cls).value}-class code on a "
                f"settlement that must be {want.value}")


# --------------------------------------------------------------------------
# Cost and failure reporting -- the numbers this project is judged on
# --------------------------------------------------------------------------

def test_thinking_tokens_are_billed():
    """The single most-publicised finding of the live run had no test at all.

    Reasoning is billed at the output rate and is absent from output_tokens.
    Counting only output understated the one measured run by 5.8x.
    """
    u = Usage(model="gemini-3.5-flash")
    u.record(inp=885, out=337, thought=2308)
    assert u.billable_output == 337 + 2308
    without = (885 / 1e6 * 1.50) + (337 / 1e6 * 9.00)
    assert u.usd == pytest.approx(0.025133, abs=1e-6)
    assert u.usd / without == pytest.approx(5.76, abs=0.05)


def test_an_unpriced_model_reports_no_cost():
    u = Usage(model="some-model-nobody-priced")
    u.record(inp=100, out=100)
    assert u.usd is None
    assert u.to_dict(126)["price_known"] is False


def test_a_capped_run_is_never_extrapolated():
    u = Usage(model="gemini-3.5-flash")
    u.record(inp=885, out=337, thought=2308)
    u.successes = 2
    assert u.per_n_records(126, complete=False) == {}
    assert u.per_n_records(126, complete=True)


def test_a_run_that_produced_nothing_reports_no_cost_per_note():
    """Three of four failure shapes exited 0 with a real dollar figure printed.

    `calls` counts attempts, because the tokens are spent either way -- so it
    could never be the gate. A refusal, a truncation and a non-completed status
    all record a call and produce no note.
    """
    u = Usage(model="gemini-3.5-flash")
    u.record(inp=885, out=337, thought=2308)
    d = u.to_dict(126, complete=False)
    assert d["successes"] == 0
    assert d["usd_per_note"] is None
    assert d["per_100_records"] == {}


def test_cost_per_note_divides_by_notes_not_attempts():
    u = Usage(model="gemini-3.5-flash")
    for _ in range(19):
        u.record(inp=100, out=50, thought=100)
    u.successes = 1
    d = u.to_dict(126)
    assert d["usd_per_note"] == pytest.approx(d["usd_per_call"] * 19, rel=1e-6)


class _AlwaysLimited:
    """A client that is rate limited on every attempt, forever."""

    def __init__(self):
        self.attempts = 0
        self.interactions = self

    def create(self, **kw):
        self.attempts += 1
        raise RuntimeError("429 RateLimitError: please retry in 0.01s")


def test_skipped_calls_are_not_reported_as_model_failures(monkeypatch):
    """Nineteen never-sent calls printed as "api errors 19" is a false fact.

    Indistinguishable from nineteen refusals, and the exit-3 message then said
    "not one of the 19 model calls produced usable output" when two calls were
    made and seventeen were skipped.

    This drives the real circuit breaker rather than setting the counters by
    hand. The first version of this test assigned `usage.skipped` directly and
    passed whether the breaker counted skips or errors -- the same non-test the
    rest of this file exists to catch, written while writing the file that
    catches it.
    """
    monkeypatch.setattr(llm, "GEMINI_MIN_INTERVAL", 0.0)
    client = _AlwaysLimited()
    backend = llm.GeminiBackend(client)
    usage = Usage(model="gemini-3.5-flash")

    for _ in range(19):
        assert backend.complete("gemini-3.5-flash", "sys", "prompt",
                                {}, 4096, usage) is None

    assert usage.skipped == 17, (
        f"the breaker stopped after 2 calls but counted {usage.skipped} as "
        f"skipped; the other {19 - 2 - usage.skipped} are being reported as "
        f"model failures")
    assert usage.errors < 19
    assert usage.gave_up, "the run stopped early and did not say so"
    assert client.attempts <= 2 * (llm.MAX_RETRIES + 1), (
        "the breaker did not stop the run: a spent daily quota would grind "
        "through backoff for the better part of an hour")


# --------------------------------------------------------------------------
# N4 -- a rate over an empty population is not a result
# --------------------------------------------------------------------------

def test_a_batch_with_nothing_to_catch_does_not_print_a_pass(tmp_path):
    """--quick produces no must-escalate class, and printed 0.0% [PASS].

    In a report whose argument is that a rate over nothing means nothing, this
    was the one place a rate over nothing was still printed as a result.
    """
    write_dataset(tmp_path, seed=42, n_settlements=8)
    findings, m, _u, timing, _a = run(tmp_path)
    metrics = score(findings, load_truth(tmp_path))
    if metrics["must_escalate_total"]:
        pytest.skip("quick batch happened to contain a must-escalate class")
    text = render_console(metrics, findings, timing, m)
    assert "[PASS]" not in text
    assert "NOTHING THAT MUST ESCALATE" in text


def test_the_false_escalate_denominator_is_should_reconcile(tmp_path):
    """Dividing by every settlement was the flattering choice, and invisible."""
    d = _dataset(tmp_path)
    findings, m, _u, timing, _a = run(d)
    metrics = score(findings, load_truth(d))
    assert metrics["should_reconcile_total"] < metrics["settlements"]
    text = render_console(metrics, findings, timing, m)
    assert f"of {metrics['should_reconcile_total']} that should have reconciled" in text


# --------------------------------------------------------------------------
# M6 -- the stop_reason guard, which nothing exercised
# --------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["refusing", "truncated"])
def test_a_reply_the_model_did_not_finish_is_rejected_by_the_guard(scenario):
    """Deleting the stop_reason check used to leave every test green.

    The `refusing` stub returned empty text, so `json.loads(text) if text else
    None` already returned None one line later and the guard was never reached.
    Both scenarios now return content that WOULD parse, so the stop_reason is
    the only thing that can reject them -- which is what the test is for.
    """
    from recon.agent.fake import ScriptedClient

    client = ScriptedClient(scenario)
    usage = Usage(model="claude-opus-5")
    backend = llm.AnthropicBackend(client)
    out = backend.complete("claude-opus-5", "sys", "prompt", {}, 4096, usage)

    assert out is None, (
        f"a {scenario} reply parsed as a usable note; the guard is not the "
        f"thing rejecting it")
    assert usage.errors == 1
    assert usage.successes == 0


# --------------------------------------------------------------------------
# The two headline fixes from the adversarial holdout
# --------------------------------------------------------------------------

def test_a_transfer_charge_is_not_counted_against_the_payout(tmp_path):
    """Described in FAILURE_LOG as "pinned with regression tests". It was not.

    A charge is a fee ON the transfer, not part of the payout being
    transferred. Counting it inside the surplus made a Rs 23.60 charge on a
    consolidated transfer break the exact-sum requirement, so neither payout
    resolved and both were reported as never paid -- two false escalations from
    one bank fee.
    """
    write_holdout(tmp_path, seed=1337)
    findings, _m, _u, _t, _a = run(tmp_path)
    metrics = score(findings, load_truth(tmp_path))
    assert metrics["false_escalate_count"] == 0, [
        f.settlement_id for f, _ in metrics.get("_false_escalate", [])]
    consolidated = [f for f in findings
                    if f.reason_code is AnomalyClass.CONSOLIDATED_PAYOUT]
    assert consolidated, "holdout should resolve consolidated payouts"
    for f in consolidated:
        assert f.disposition is Disposition.RECONCILED


# --------------------------------------------------------------------------
# A stub must never occupy the file that holds a measurement
# --------------------------------------------------------------------------

def test_a_stub_run_cannot_overwrite_the_committed_measurement(tmp_path):
    """It did, and the result was pushed to a public repository.

    `--llm-stub hallucinating` wrote its fabricated token counts straight over
    out/agent.json, which is the only live API evidence this project has and
    which the README cites by number. Nothing noticed: the file still parsed,
    still had a model name and a token count, and still looked like a run. The
    two now have different filenames, so the collision cannot happen.
    """
    from recon.cli import main as cli_main

    d = _dataset(tmp_path)
    out = tmp_path / "out"
    real = {"usage": {"model": "gemini-3.5-flash", "calls": 2}, "is_stub": False}
    out.mkdir()
    (out / "agent.json").write_text(json.dumps(real), encoding="utf-8")

    rc = cli_main(["--input", str(d), "--out", str(out), "--quiet",
                   "--llm", "--llm-stub", "hallucinating"])
    assert rc == 0

    after = json.loads((out / "agent.json").read_text(encoding="utf-8"))
    assert after == real, "a scripted stub overwrote a real measurement"
    assert (out / "agent-stub.json").exists(), "stub output went nowhere"
    stub = json.loads((out / "agent-stub.json").read_text(encoding="utf-8"))
    assert stub["is_stub"] is True


# --------------------------------------------------------------------------
# The vendor call must use arguments the vendor's SDK accepts
# --------------------------------------------------------------------------

class _RecordingInteractions:
    """Captures exactly what GeminiBackend passes to the SDK."""

    def __init__(self):
        self.kwargs = None
        self.interactions = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        raise RuntimeError("captured; not sending")


def test_gemini_call_uses_arguments_the_sdk_accepts():
    """A stub built to one vendor's wire shape cannot check the other's.

    `max_output_tokens=` was added to GeminiBackend to close a review finding
    that the parameter was being dropped. It is not an argument
    interactions.create() takes: the SDK raises TypeError before any request
    goes out. A 19-call batch produced 19 errors, 0 calls and 0 tokens, and
    every test still passed, because the only test of the agent layer drives a
    scripted client written to the Anthropic shape.

    The first version of THIS test also missed it, because _attempt catches
    every exception by design, so a bad signature looks like any other error
    from the outside. It therefore captures the real call site's arguments and
    replays them against the real SDK. An unexpected keyword raises TypeError
    locally, so no request is made and no quota is spent.
    """
    genai = pytest.importorskip(
        "google.genai", reason="google-genai is an optional dependency")

    recorder = _RecordingInteractions()
    backend = llm.GeminiBackend(recorder)
    usage = Usage(model="gemini-3.7-flash")
    with mock.patch.object(llm, "GEMINI_MIN_INTERVAL", 0.0):
        backend._attempt("gemini-3.7-flash", "sys", "prompt",
                         {"type": "object"}, usage, 4096, last=True)

    assert recorder.kwargs is not None, "the backend never called the SDK"

    real = genai.Client(api_key="dummy-key-not-real")
    try:
        real.interactions.create(**recorder.kwargs)
    except TypeError as e:
        pytest.fail(
            f"GeminiBackend calls interactions.create with arguments the "
            f"installed SDK does not accept: {e}; "
            f"passed: {sorted(recorder.kwargs)}")
    except Exception:
        pass          # reached the network or auth layer: the signature is fine


def test_the_gemini_client_is_built_with_a_request_timeout():
    """Without one the SDK waits on a stalled call forever.

    A 19-call batch ran for over half an hour, wrote nothing and reported
    nothing. No output is produced until the batch finishes, so interrupting it
    loses whatever quota was already spent. MAX_BACKOFF_S in llm.py already says
    a run that hangs for ten minutes is its own kind of failure; it capped the
    sleep between attempts and left the attempts uncapped.
    """
    genai = pytest.importorskip(
        "google.genai", reason="google-genai is an optional dependency")

    captured = {}
    real_client = genai.Client

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_client(api_key="dummy-key-not-real", **kwargs)

    with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "dummy-key-not-real"}), \
            mock.patch.object(genai, "Client", spy):
        llm.build_client("gemini")

    opts = captured.get("http_options")
    assert opts is not None, "the Gemini client is built with no http_options"
    assert opts.timeout, "the Gemini client is built with no request timeout"
    assert opts.timeout == int(llm.GEMINI_TIMEOUT_S * 1000)
