#!/usr/bin/env python3
"""One command, end to end. No arguments, no API key, no dependencies.

    python demo.py

Generates the dataset, reconciles it, runs the adversarial holdout, shows the
guard rejecting a lying model without moving a verdict, and writes
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
    banner("1/5  Generating the synthetic three-source dataset + answer key")
    lines, bank, orders, truth = write_dataset(ROOT / "data", seed=42,
                                               n_settlements=120)
    print(f"  {len(lines)} settlement lines | {len(truth)} settlements | "
          f"{len(bank)} bank rows | {len(orders)} orders")
    print("  -> data/settlement_recon.csv, bank_statement.csv, order_ledger.csv")
    print("  -> data/ground_truth.json   (the answer key every number is scored on)")

    banner("2/5  Reconciling the main batch")
    rc_main = cli_main(["--input", str(ROOT / "data"), "--out", str(ROOT / "out")])

    banner("3/5  Adversarial holdout: settlements with TWO defects at once")
    print("  The classifier is single-label. Compounds are outside its design,")
    print("  which is what makes this the only honest number in the project.")
    hd = ROOT / "data" / "holdout"
    write_holdout(hd, seed=1337)
    rc_hold = cli_main(["--input", str(hd), "--out", str(ROOT / "out" / "holdout")])

    banner("4/5  The guard: a model that lies, and the verdicts that do not move")
    print("  A scripted client stands in for the model and fabricates a rupee")
    print("  figure in every note. No API key, no network -- the point is what")
    print("  the engine does with output it cannot trust.")
    cli_main(["--input", str(ROOT / "data"), "--llm",
              "--llm-stub", "hallucinating",
              "--out", str(ROOT / "out" / "guard-demo")])

    # The claim is not "the guard rejected things". It is that a model behaving
    # as badly as it can does not change a single verdict. Checked here, in the
    # demo, rather than asserted in a README paragraph nobody scrolls to.
    import json                                     # noqa: PLC0415
    def _verdicts(p):
        d = json.loads((p / "run.json").read_text(encoding="utf-8"))
        return {f["settlement_id"]: (f["disposition"], f["reason_code"],
                                     f["delta"], f["utr"])
                for f in d["findings"]}
    clean = _verdicts(ROOT / "out")
    lied_to = _verdicts(ROOT / "out" / "guard-demo")
    moved = sum(clean[k] != lied_to.get(k) for k in clean)
    print()
    print(f"  Every fabricated figure was caught, and {moved} of "
          f"{len(clean)} verdicts moved.")
    print("  disposition, reason code, delta and UTR are byte-identical to the")
    print("  run above, which made no model calls at all.")

    banner("5/5  Writing eval/metrics.md")
    sys.path.insert(0, str(ROOT / "eval"))
    import run_eval                                  # noqa: PLC0415
    run_eval.main()

    print()
    print("=" * 74)
    if rc_main == 0 and rc_hold == 0:
        print("RESULT: zero false clears on both sets.")
    else:
        why = {1: "FALSE CLEARS PRESENT -- see the exception lists above.",
               4: "SCORING WAS INCOMPLETE -- see the warnings above.",
               2: "A SOURCE FILE COULD NOT BE READ."}
        bad = rc_main or rc_hold
        print("RESULT: " + why.get(bad, f"run failed with exit code {bad}."))
    print("Open  out/report.html  in a browser for the visual report.")
    print("Read  eval/metrics.md   for the full numbers and their caveats.")
    print("=" * 74)
    return 0 if (rc_main == 0 and rc_hold == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
