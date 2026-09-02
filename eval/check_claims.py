"""Assert the README's headline numbers are what the code actually produces.

Three independent audits found the same shape of problem more often than any
other: a number in the documentation that had quietly stopped matching the
code, with nothing in the test suite able to notice. Two of the three called it
the most severe class of finding available, because the whole argument of this
project is that its numbers are checkable.

So the numbers are checked. This runs the real pipeline over both committed
datasets, scrapes every figure out of the README's headline table, and fails if
any of them disagree. It is deliberately dumb about parsing: if the table is
reworded so a claim can no longer be found, that is a failure too, not a pass.

    python eval/check_claims.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recon.engine.pipeline import run          # noqa: E402
from recon.report import load_truth, score     # noqa: E402


def measure(datadir: Path) -> dict:
    findings, m, _units, _timing, _agent = run(datadir)
    metrics = score(findings, load_truth(datadir))
    return {
        "settlements": metrics["settlements"],
        "false_clear_count": metrics["false_clear_count"],
        "must_escalate_total": metrics["must_escalate_total"],
        "false_escalate_count": metrics["false_escalate_count"],
        "should_reconcile_total": metrics["should_reconcile_total"],
        "match_rate": metrics["match_rate"],
        "classification_accuracy": metrics["classification_accuracy"],
        "orphan_bank": len(m.orphan_bank),
    }


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def row(readme: str, label: str) -> tuple[str, str]:
    """The two cells of one table row, or a hard failure.

    `label` is a regex, so a row whose text carries markdown emphasis can be
    matched without the emphasis leaking into the comparison.
    """
    m = re.search(rf"^\|\s*{label}\s*\|([^|]*)\|([^|]*)\|\s*$",
                  readme, re.M)
    if not m:
        raise SystemExit(
            f"FAIL: no row labelled {label!r} in the README's headline table.\n"
            f"      A claim that cannot be located cannot be checked, so this "
            f"is a failure rather than a skip.")
    return m.group(1).strip(), m.group(2).strip()


def main() -> int:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    main_set = measure(ROOT / "data")
    holdout = measure(ROOT / "data" / "holdout")

    checks: list[tuple[str, str, str]] = []          # label, claimed, actual

    a, b = row(readme, "Settlements")
    checks += [("settlements (main)", a, str(main_set["settlements"])),
               ("settlements (holdout)", b, str(holdout["settlements"]))]

    a, b = row(readme, r"\*\*False-clear rate\*\*")
    for name, cell, s in (("main", a, main_set), ("holdout", b, holdout)):
        want = (f"**{pct(s['false_clear_count'] / s['must_escalate_total'])}** "
                f"({s['false_clear_count']}/{s['must_escalate_total']})")
        checks.append((f"false-clear rate ({name})", cell, want))

    a, b = row(readme, "False-escalate rate")
    for name, cell, s in (("main", a, main_set), ("holdout", b, holdout)):
        want = (f"{pct(s['false_escalate_count'] / s['should_reconcile_total'])} "
                f"({s['false_escalate_count']}/{s['should_reconcile_total']})")
        checks.append((f"false-escalate rate ({name})", cell, want))

    # The holdout cell carries a label after the figure, so that 50% does not
    # read as failure to someone skimming. Compare the figure only.
    a, b = row(readme, "Match rate")
    checks += [("match rate (main)", a.split()[0], pct(main_set["match_rate"])),
               ("match rate (holdout)", b.split()[0], pct(holdout["match_rate"]))]

    a, _b = row(readme, "Unexplained bank rows")
    checks.append(("unexplained bank rows (main)", a,
                   f"{main_set['orphan_bank']}, all reported"))

    a, b = row(readme, "Reason-code accuracy")
    checks += [
        ("reason-code accuracy (main)", a,
         pct(main_set["classification_accuracy"])),
        ("reason-code accuracy (holdout)", b.split(",")[0],
         pct(holdout["classification_accuracy"])),
    ]

    # The committed model measurement. The README quotes these numbers as the
    # only live API evidence in the repository, so they are checked against the
    # file rather than trusted. A stub run once overwrote this file with
    # fabricated token counts and the result was pushed public; the CLI now
    # writes stub output under a different name, and this catches it if that
    # ever stops working.
    agent_path = ROOT / "out" / "agent.json"
    if agent_path.exists():
        agent = json.loads(agent_path.read_text(encoding="utf-8"))
        u = agent.get("usage", {})
        if agent.get("is_stub") or "STUB" in str(u.get("model", "")).upper():
            raise SystemExit(
                "FAIL: out/agent.json holds SCRIPTED STUB output, not a "
                "measurement.\n"
                "      The README cites this file as its only live API "
                "evidence.\n"
                "      Restore the real run, or delete the claim.")
        per100 = (u.get("per_100_records") or {}).get("usd")
        for label, pattern, actual in (
            ("model", r"`(gemini-[\d.]+-flash)`", str(u.get("model"))),
            ("input tokens", r"([\d,]+) input", str(u.get("input_tokens"))),
            ("output tokens", r"([\d,]+) output", str(u.get("output_tokens"))),
            ("thinking tokens", r"([\d,]+) thinking",
             str(u.get("thought_tokens"))),
            # The cost per 100 records is the claim this section exists to make,
            # and the one an earlier draft got wrong by publishing an
            # extrapolation as a measurement. It is checked against the file.
            ("cost per 100 records", r"\$(0\.\d+) per 100 records",
             f"{per100:.4f}" if per100 else "(not reported)"),
            ("calls", r"(\d+) of \1 notes accepted", str(u.get("calls"))),
        ):
            m = re.search(pattern, readme)
            if m:
                checks.append((f"agent.json {label}",
                               m.group(1).replace(",", ""), actual))

    # The test count, which drifts every time the suite grows.
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--collect-only"],
                       cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"(\d+) tests? collected", r.stdout)
    if m:
        claimed = re.search(r"(\d+) tests\. No API key", readme)
        if not claimed:
            raise SystemExit("FAIL: the README no longer states a test count.")
        checks.append(("test count", claimed.group(1), m.group(1)))

    width = max(len(c[0]) for c in checks)
    bad = 0
    for label, claimed, actual in checks:
        ok = claimed == actual
        bad += not ok
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark}  {label:<{width}}  README {claimed!r}"
              + ("" if ok else f"  !=  measured {actual!r}"))

    print()
    if bad:
        print(f"{bad} of {len(checks)} README claims do not match what the "
              f"code produces.")
        return 1
    print(f"all {len(checks)} README claims match what the code produces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
