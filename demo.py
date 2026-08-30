#!/usr/bin/env python3
"""One command, end to end. No arguments, no API key, no dependencies.

    python demo.py

Generates the dataset, reconciles it, runs the adversarial holdout, and writes
eval/metrics.md. Pure standard library, so it runs on a fresh clone with nothing
installed but Python 3.11+.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from recon.adversarial import write_holdout      # noqa: E402
from recon.cli import main as cli_main           # noqa: E402
from recon.generate import write_dataset         # noqa: E402


def banner(text: str):
    print()
    print("#" * 74)
    print(f"# {text}")
    print("#" * 74)


def main() -> int:
    banner("1/4  Generating the synthetic three-source dataset + answer key")
    lines, bank, orders, truth = write_dataset(ROOT / "data", seed=42,
                                               n_settlements=120)
    print(f"  {len(lines)} settlement lines | {len(truth)} settlements | "
          f"{len(bank)} bank rows | {len(orders)} orders")
    print("  -> data/settlement_recon.csv, bank_statement.csv, order_ledger.csv")
    print("  -> data/ground_truth.json   (the answer key every number is scored on)")

    banner("2/4  Reconciling the main batch")
    rc_main = cli_main(["--input", str(ROOT / "data"), "--out", str(ROOT / "out")])

    banner("3/4  Adversarial holdout: settlements with TWO defects at once")
    print("  The classifier is single-label. Compounds are outside its design,")
    print("  which is what makes this the only honest number in the project.")
    hd = ROOT / "data" / "holdout"
    write_holdout(hd, seed=1337)
    rc_hold = cli_main(["--input", str(hd), "--out", str(ROOT / "out" / "holdout")])

    banner("4/4  Writing eval/metrics.md")
    sys.path.insert(0, str(ROOT / "eval"))
    import run_eval                                  # noqa: PLC0415
    run_eval.main()

    print()
    print("=" * 74)
    if rc_main == 0 and rc_hold == 0:
        print("RESULT: zero false clears on both sets.")
    else:
        print("RESULT: FALSE CLEARS PRESENT -- see the exception lists above.")
    print("Read eval/metrics.md for the full numbers and their caveats.")
    print("=" * 74)
    return 0 if (rc_main == 0 and rc_hold == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
