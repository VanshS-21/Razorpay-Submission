"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine.pipeline import run as run_pipeline
from .ingest import IngestError
from .report import load_truth, render_console, score, write_json
from .report_html import write_html


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="recon",
        description="Three-way settlement reconciliation over a batch.")
    p.add_argument("--input", default="data", help="directory holding the three CSVs")
    p.add_argument("--out", default="out", help="directory for JSON output")
    p.add_argument("--llm", action="store_true",
                   help="enable the agent layer (needs ANTHROPIC_API_KEY). "
                        "Off by default: the deterministic engine is the product.")
    p.add_argument("--provider", default="auto",
                   choices=["auto", "anthropic", "gemini"],
                   help="which vendor answers. 'auto' picks whichever API key "
                        "is in the environment. The verdicts must not depend "
                        "on this choice -- that is the point of it.")
    p.add_argument("--model", default=None,
                   help="model id for the agent layer "
                        "(default: per provider, see llm.DEFAULT_MODELS)")
    p.add_argument("--narrate-limit", type=int, default=None,
                   help="cap how many exceptions get an LLM-written note")
    p.add_argument("--llm-stub", default=None, dest="stub",
                   choices=["honest", "hallucinating", "overreaching",
                            "failing", "refusing", "truncated",
                            "plausible"],
                   help="drive the agent code path with a SCRIPTED client "
                        "instead of a real model. Exercises the guard offline. "
                        "Produces no measurement of model quality or cost.")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)

    datadir = Path(a.input)
    if not (datadir / "settlement_recon.csv").exists():
        print(f"error: no dataset in {datadir}/ -- run:  python -m recon.generate",
              file=sys.stderr)
        return 2

    try:
        findings, m, units, timing, agent = run_pipeline(
            datadir, use_llm=a.llm, model=a.model,
            narrate_limit=a.narrate_limit, stub=a.stub, provider=a.provider)
    except IngestError as e:
        # A bad source file is a user's problem to fix, so it gets a sentence
        # naming the file, the row and the column -- not a stack trace.
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        # LLMUnavailable and anything else from the agent layer. The failure is
        # reported rather than silently degraded, so a run that was supposed to
        # use the model never quietly pretends it did.
        from .agent.llm import LLMUnavailable
        if isinstance(e, LLMUnavailable):
            print(f"error: --llm requested but unavailable: {e}", file=sys.stderr)
            print("       the deterministic engine runs without it: drop --llm",
                  file=sys.stderr)
            return 3
        raise

    truthfile = datadir / "ground_truth.json"
    if not truthfile.exists():
        for f in findings:
            print(f"{f.settlement_id}\t{f.disposition.value}\t{f.reason_code.value}")
        return 0

    metrics = score(findings, load_truth(datadir))
    if not a.quiet:
        print(render_console(metrics, findings, timing, m))
        if agent:
            print(_render_agent(agent))

    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    write_json(outdir / "run.json", metrics, findings, timing, m)
    write_html(outdir / "report.html", metrics, findings, timing,
               units, agent, dataset=str(datadir), match_result=m)
    if agent:
        (outdir / "agent.json").write_text(json.dumps(agent, indent=2),
                                           encoding="utf-8")
    if not a.quiet:
        print(f"\nwrote {outdir / 'run.json'}")
        print(f"wrote {outdir / 'report.html'}   <- open this in a browser")

    # A run that asked for the model and got nothing from it did not do what it
    # was told, however good the reconciliation underneath was.
    u = agent["usage"] if agent else {}
    if a.llm and agent and not u["successes"] and (u["errors"] or u.get("skipped")):
        # Count what actually happened. Reporting skipped calls as failed ones
        # said "not one of the 19 model calls produced usable output" when two
        # calls were made and seventeen were never sent.
        made, skipped = u["calls"], u.get("skipped", 0)
        print(f"error: --llm was requested but no model output reached this "
              f"report: {made} call(s) made, none usable"
              + (f", {skipped} never attempted" if skipped else ""),
              file=sys.stderr)
        if u.get("gave_up"):
            print(f"       {u['gave_up']}", file=sys.stderr)
        return 3

    if metrics["false_clear_count"]:
        return 1
    # A green exit code has to mean "everything was checked and everything was
    # fine". Scoring that skipped units, or scored none at all, is not that --
    # and a CI gate reading only the exit code would never know.
    if (metrics["unscored"] or metrics["unmatched_key"]
            or not metrics["scored"] or not metrics["must_escalate_total"]):
        print("error: scoring was incomplete -- see the warnings above; "
              "the false-clear rate does not cover every settlement",
              file=sys.stderr)
        return 4
    return 0


def _render_agent(a: dict) -> str:
    w = 74
    g, u = a["guard"], a["usage"]
    out = ["", "AGENT LAYER", "-" * w]
    if a.get("is_stub"):
        out.append("  *** SCRIPTED STUB -- NOT A REAL MODEL CALL. ***")
        out.append("  *** Exercises the guard offline. The token and cost")
        out.append("  *** figures below are fabricated by the stub and are")
        out.append("  *** NOT a measurement of anything.")
        out.append("-" * w)
    out.append(f"  provider              {a.get('provider', '?')}")
    out.append(f"  model                 {a['model']}")
    out.append(f"  narration notes       {a['narrations_accepted']} accepted")
    out.append(f"  identity leads        {a['matches_proposed']} proposed "
               f"(advisory only -- never auto-cleared)")
    out.append(f"  guard rejections      {g['rejected']}/{g['checked']} "
               f"({g['rejection_rate'] * 100:.1f}%)")
    if g["reasons"]:
        for why, n in sorted(g["reasons"].items(), key=lambda kv: -kv[1]):
            out.append(f"      {why}: {n}")
    if u.get("thought_tokens"):
        out.append(f"  thinking tokens       {u['thought_tokens']:,} "
                   f"(billed at the output rate; invisible in the reply)")
    per = u.get("per_100_records") or {}
    # Never print a cost for calls that did not happen, or for a model whose
    # price we have not actually read. "$0.0000" reads as "free" rather than as
    # "unknown", and both are the wrong thing to put beside real token counts.
    # `successes`, not `calls`: a capped run in which every call was refused or
    # truncated still had calls, and printed a real dollar figure directly
    # beside "0 notes accepted".
    if (not a.get("is_stub") and u.get("successes") and u.get("price_known")
            and not u.get("batch_complete")):
        out.append(f"  cost per note         ${u['usd_per_note']:.5f} "
                   f"(measured over {u['successes']} note(s) from "
                   f"{u['calls']} call(s), of "
                   f"{a.get('exceptions_total', '?')} exceptions)")
        out.append("  cost per 100 records  NOT REPORTED -- this run was capped "
                   "by --narrate-limit,")
        out.append("                        so scaling it to a full batch would "
                   "understate the cost.")
    if per and not a.get("is_stub") and u.get("successes") and u.get("price_known"):
        out.append(f"  cost per 100 records  ${per['usd']:.4f} "
                   f"(~Rs {per['inr']:.2f})")
    if a.get("is_stub"):
        out.append(f"  calls                 {u['calls']} (scripted, no network)")
    else:
        out.append(f"  tokens                {u['input_tokens']:,} in / "
                   f"{u['output_tokens']:,} out over {u['calls']} calls")
    if u.get("throttled"):
        out.append(f"  rate limited          {u['throttled']} (waited and retried)")
    if u["errors"]:
        out.append(f"  api errors            {u['errors']}")
    if u.get("skipped"):
        out.append(f"  not attempted         {u['skipped']} "
                   f"(the run stopped early; these were never sent)")
    if u.get("gave_up"):
        out.append(f"  ** {u['gave_up']}")
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
