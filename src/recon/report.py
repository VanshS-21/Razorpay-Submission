"""Score a run against the ground-truth key and render the results.

The headline number here is not the match rate. It is the FALSE-CLEAR RATE:
units the engine called reconciled that the key says needed a human. In a
finance system that is the expensive error -- a missed exception is money quietly
lost, while an unnecessary exception costs only review time. Reporting the
flattering number first and burying the dangerous one would be exactly the kind
of cherry-picking this project is supposed to avoid, so the order is fixed.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .models import AnomalyClass, Disposition, Finding, rupees


def load_truth(datadir: Path) -> dict:
    raw = json.loads((Path(datadir) / "ground_truth.json").read_text(encoding="utf-8"))
    return {r["settlement_id"]: r for r in raw}


def score(findings: list[Finding], truth: dict) -> dict:
    """Compare verdicts against the key. Returns a metrics dict."""
    total = len(findings)
    reconciled = sum(1 for f in findings if f.disposition is Disposition.RECONCILED)

    false_clear = []     # said reconciled, key says exception  <- the dangerous one
    false_escalate = []  # said exception, key says reconciled  <- the annoying one
    class_correct = 0

    per_class = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "support": 0})

    # A settlement the key says nothing about cannot be scored -- but silently
    # skipping it lets a run report 0.0% false clears on data it never checked.
    # Both directions of that gap are counted and surfaced; see render_console.
    unscored = [f.settlement_id for f in findings if f.settlement_id not in truth]
    seen = {f.settlement_id for f in findings}
    unmatched_key = [sid for sid in truth if sid not in seen]

    strict_correct = 0

    for f in findings:
        gt = truth.get(f.settlement_id)
        if not gt:
            continue
        want_disp = gt["expected_disposition"]
        want_cls = gt["true_class"]
        # A compound carries two real defects, so naming either is defensible.
        # Empty on the main set, where exactly one code is correct.
        acceptable = {want_cls, *gt.get("also_acceptable", ())}

        per_class[want_cls]["support"] += 1
        if f.reason_code.value == want_cls:
            strict_correct += 1
        if f.reason_code.value in acceptable:
            class_correct += 1
            per_class[want_cls]["tp"] += 1
        else:
            per_class[want_cls]["fn"] += 1
            per_class[f.reason_code.value]["fp"] += 1

        if f.disposition.value == "reconciled" and want_disp == "exception":
            false_clear.append((f, gt))
        elif f.disposition.value == "exception" and want_disp == "reconciled":
            false_escalate.append((f, gt))

    # Denominator is the units actually scored. Counting key entries that
    # produced no finding would push the rate DOWN for data the engine never
    # even saw -- an error that can only ever flatter the headline.
    must_escalate_total = sum(1 for sid, g in truth.items()
                              if sid in seen
                              and g["expected_disposition"] == "exception")
    scored = total - len(unscored)

    rows = []
    for cls, c in sorted(per_class.items(), key=lambda kv: -kv[1]["support"]):
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        prec = tp / (tp + fp) if (tp + fp) else None
        rec = tp / (tp + fn) if (tp + fn) else None
        rows.append({
            "class": cls, "support": c["support"],
            "precision": prec, "recall": rec, "tp": tp, "fp": fp, "fn": fn,
        })

    return {
        "settlements": total,
        "reconciled": reconciled,
        "match_rate": reconciled / total if total else 0.0,
        "scored": scored,
        "unscored": unscored,
        "unmatched_key": unmatched_key,
        "classification_accuracy": class_correct / scored if scored else 0.0,
        "classification_accuracy_strict": strict_correct / scored if scored else 0.0,
        "false_clear_count": len(false_clear),
        "false_clear_rate": len(false_clear) / must_escalate_total if must_escalate_total else 0.0,
        "false_escalate_count": len(false_escalate),
        "false_escalate_rate": len(false_escalate) / total if total else 0.0,
        "must_escalate_total": must_escalate_total,
        "per_class": rows,
        "_false_clear": false_clear,
        "_false_escalate": false_escalate,
    }


def _pct(x):
    return "  --  " if x is None else f"{x * 100:5.1f}%"


def render_console(metrics: dict, findings: list[Finding], timing: dict,
                   match_result=None) -> str:
    out = []
    w = 74
    out.append("=" * w)
    out.append("SETTLEMENT RECONCILIATION -- RUN REPORT".center(w))
    out.append("=" * w)

    # Safety first, deliberately.
    fc = metrics["false_clear_count"]
    out.append("")
    out.append("SAFETY  (the metric that matters most)")
    out.append("-" * w)
    verdict = "PASS" if fc == 0 else "FAIL"
    out.append(f"  False-clear rate      {_pct(metrics['false_clear_rate'])}   "
               f"({fc} of {metrics['must_escalate_total']} must-escalate units)  [{verdict}]")
    out.append(f"  False-escalate rate   {_pct(metrics['false_escalate_rate'])}   "
               f"({metrics['false_escalate_count']} units sent to a human unnecessarily)")
    if metrics["unscored"] or metrics["unmatched_key"] or not metrics["scored"]:
        out.append("")
        out.append("  !! THE RATE ABOVE IS NOT A CLEAN BILL OF HEALTH.")
        if metrics["unscored"]:
            out.append(f"  !! {len(metrics['unscored'])} settlement(s) are absent from the "
                       f"answer key and were NOT scored:")
            for sid in metrics["unscored"][:5]:
                out.append(f"  !!     {sid}")
            if len(metrics["unscored"]) > 5:
                out.append(f"  !!     ... and {len(metrics['unscored']) - 5} more")
        if metrics["unmatched_key"]:
            out.append(f"  !! {len(metrics['unmatched_key'])} key entr(ies) produced no "
                       f"finding at all -- the engine never saw them.")
        if not metrics["scored"]:
            out.append("  !! NOTHING was scored. A 0.0% rate over zero units means nothing.")

    out.append("")
    out.append("THROUGHPUT")
    out.append("-" * w)
    rate = timing["lines"] / timing["total_s"] if timing["total_s"] else 0
    out.append(f"  {timing['lines']} lines / {timing['settlements']} settlements "
               f"in {timing['total_s']:.3f}s  ({rate:,.0f} lines/sec)")
    out.append(f"  load {timing['load_s']:.3f}s | match {timing['match_s']:.3f}s "
               f"| classify {timing['classify_s']:.3f}s")

    out.append("")
    out.append("COVERAGE")
    out.append("-" * w)
    out.append(f"  Match rate                {_pct(metrics['match_rate'])}  "
               f"({metrics['reconciled']}/{metrics['settlements']} auto-reconciled)")
    out.append(f"  Classification accuracy   {_pct(metrics['classification_accuracy'])}  "
               f"(correct reason code)")
    if metrics["classification_accuracy_strict"] != metrics["classification_accuracy"]:
        out.append(f"    of which exact primary   "
                   f"{_pct(metrics['classification_accuracy_strict'])}  "
                   f"(rest named a defensible second defect on a compound)")

    if match_result is not None:
        rows = list(match_result.orphan_bank)
        cr = sum(r.credit for r in rows)
        dr = sum(r.debit for r in rows)
        out.append("")
        out.append("BANK-SIDE COVERAGE")
        out.append("-" * w)
        out.append(f"  Statement rows left unexplained   {len(rows)}")
        out.append(f"    unmatched credits               {rupees(cr)}")
        out.append(f"    unmatched debits                {rupees(dr)}")
        out.append("  Most are unrelated account traffic (payroll, other PSPs,")
        out.append("  vendor payments). The engine does not claim to know which;")
        out.append("  it reports them so nothing leaves the account unseen.")

    out.append("")
    out.append("PER-CLASS")
    out.append("-" * w)
    out.append(f"  {'class':26} {'n':>4}  {'precision':>10} {'recall':>8}")
    for r in metrics["per_class"]:
        if r["support"] == 0 and r["fp"] == 0:
            continue
        out.append(f"  {r['class']:26} {r['support']:>4}  "
                   f"{_pct(r['precision']):>10} {_pct(r['recall']):>8}")

    if metrics["_false_clear"]:
        out.append("")
        out.append("!! FALSE CLEARS -- units wrongly marked reconciled")
        out.append("-" * w)
        for f, gt in metrics["_false_clear"]:
            out.append(f"  {f.settlement_id}  said={f.reason_code.value}  "
                       f"truth={gt['true_class']}")

    exceptions = [f for f in findings if f.disposition is Disposition.EXCEPTION]
    out.append("")
    out.append(f"EXCEPTION LIST  ({len(exceptions)} units need a human)")
    out.append("-" * w)
    if not exceptions:
        out.append("  (none)")
    for f in sorted(exceptions, key=lambda x: -abs(x.delta)):
        out.append(f"  {f.settlement_id}  {f.reason_code.value:24} {rupees(f.delta):>16}")
        out.append(f"      {f.explanation}")
        if f.action_required:
            out.append(f"      ACTION: {f.action_required}")

    # Cleared, but on inference rather than evidence. Visible on purpose: a
    # reconciled-by-arithmetic group is the one clear in this engine that no
    # document corroborates, and hiding it inside the match rate would be the
    # quiet kind of overclaim this project is trying not to make.
    inferred = [f for f in findings if f.resolved_by == "deterministic:inferred"]
    if inferred:
        out.append("")
        out.append(f"SPOT CHECK  ({len(inferred)} reconciled by inference, not by evidence)")
        out.append("-" * w)
        for f in inferred:
            out.append(f"  {f.settlement_id}  {f.reason_code.value:24} "
                       f"confidence {f.confidence:.2f}")
    out.append("=" * w)
    return "\n".join(out)


def write_json(path: Path, metrics: dict, findings: list[Finding], timing: dict,
               match_result=None):
    payload = {
        "timing": timing,
        "metrics": {k: v for k, v in metrics.items() if not k.startswith("_")},
        "findings": [f.to_dict() for f in findings],
    }
    if match_result is not None:
        rows = list(match_result.orphan_bank)
        payload["bank_coverage"] = {
            "unexplained_rows": len(rows),
            "unmatched_credit_paise": sum(r.credit for r in rows),
            "unmatched_debit_paise": sum(r.debit for r in rows),
            "rows": [{"txn_id": r.txn_id, "value_date": r.value_date,
                      "narration": r.narration, "ref_no": r.ref_no,
                      "credit": r.credit, "debit": r.debit} for r in rows],
        }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
