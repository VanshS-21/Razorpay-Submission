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
    p.add_argument("--model", default=None,
                   help="model id for the agent layer (default: claude-opus-5)")
    p.add_argument("--narrate-limit", type=int, default=None,
                   help="cap how many exceptions get an LLM-written note")
    p.add_argument("--llm-stub", default=None, dest="stub",
                   choices=["honest", "hallucinating", "overreaching",
                            "failing", "refusing", "plausible"],
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
            narrate_limit=a.narrate_limit, stub=a.stub)
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
    if a.llm and agent and not agent["usage"]["calls"] and agent["usage"]["errors"]:
        print(f"error: --llm was requested but every one of the "
              f"{agent['usage']['errors']} model calls failed; no model output "
              f"reached this report", file=sys.stderr)
        return 3

    if metrics["false_clear_count"]:
        return 1
    # A green exit code has to mean "everything was checked and everything was
    # fine". Scoring that skipped units, or scored none at all, is not that --
    # and a CI gate reading only the exit code would never know.
    if metrics["unscored"] or metrics["unmatched_key"] or not metrics["scored"]:
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
    out.append(f"  model                 {a['model']}")
    out.append(f"  narration notes       {a['narrations_accepted']} accepted")
    out.append(f"  identity leads        {a['matches_proposed']} proposed "
               f"(advisory only -- never auto-cleared)")
    out.append(f"  guard rejections      {g['rejected']}/{g['checked']} "
               f"({g['rejection_rate'] * 100:.1f}%)")
    if g["reasons"]:
        for why, n in sorted(g["reasons"].items(), key=lambda kv: -kv[1]):
            out.append(f"      {why}: {n}")
    per = u.get("per_100_records") or {}
    # Never print a cost for calls that did not happen. A "$0.0000" derived from
    # zero successful calls is not a cheap run, it is a broken one.
    if per and not a.get("is_stub") and u["calls"]:
        out.append(f"  cost per 100 records  ${per['usd']:.4f} "
                   f"(~Rs {per['inr']:.2f})")
    if a.get("is_stub"):
        out.append(f"  calls                 {u['calls']} (scripted, no network)")
    else:
        out.append(f"  tokens                {u['input_tokens']:,} in / "
                   f"{u['output_tokens']:,} out over {u['calls']} calls")
    if u["errors"]:
        out.append(f"  api errors            {u['errors']}")
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
