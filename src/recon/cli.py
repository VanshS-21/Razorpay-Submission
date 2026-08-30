"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine.pipeline import run as run_deterministic
from .report import load_truth, render_console, score, write_json


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="recon",
        description="Three-way settlement reconciliation over a batch.")
    p.add_argument("--input", default="data", help="directory holding the three CSVs")
    p.add_argument("--out", default="out", help="directory for JSON output")
    p.add_argument("--no-llm", action="store_true",
                   help="deterministic only (default until the agent layer lands)")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)

    datadir = Path(a.input)
    if not (datadir / "settlement_recon.csv").exists():
        print(f"error: no dataset in {datadir}/ -- run:  python -m recon.generate",
              file=sys.stderr)
        return 2

    findings, m, units, timing = run_deterministic(datadir)

    truthfile = datadir / "ground_truth.json"
    if truthfile.exists():
        truth = load_truth(datadir)
        metrics = score(findings, truth)
        if not a.quiet:
            print(render_console(metrics, findings, timing))
        outdir = Path(a.out)
        outdir.mkdir(parents=True, exist_ok=True)
        write_json(outdir / "run.json", metrics, findings, timing)
        if not a.quiet:
            print(f"\nwrote {outdir / 'run.json'}")
        return 0 if metrics["false_clear_count"] == 0 else 1

    for f in findings:
        print(f"{f.settlement_id}\t{f.disposition.value}\t{f.reason_code.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
