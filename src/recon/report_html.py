"""Render a run as a single self-contained HTML page.

Design constraints, in order:

1. **The safety metric leads.** False-clear rate is the first thing on the page,
   at the largest size, with a pass/fail verdict. Match rate -- the flattering
   number -- comes after. A report that opens with the good news and buries the
   dangerous one is doing the reader a disservice.
2. **Every exception carries its action.** An exception list without
   instructions is just a list of problems.
3. **No network.** No CDN, no webfont, no external asset. The file opens from a
   fresh clone, offline, and looks the same on any machine.
4. **Prints.** Finance artefacts get printed and attached to emails.
"""

from __future__ import annotations

import html
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .models import AnomalyClass, Disposition, Finding, rupees

CSS = """
:root{
  --bg:#fbfbfa; --panel:#ffffff; --ink:#1a1a18; --muted:#6b6b66;
  --line:#e4e4df; --line-soft:#efefeb;
  --ok:#2f7d4f; --ok-bg:#eaf5ee;
  --bad:#b4321f; --bad-bg:#fdeeeb;
  --warn:#8a6116; --warn-bg:#fdf5e6;
  --accent:#1f5fa8; --accent-bg:#eef4fb;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#14140f; --panel:#1c1c18; --ink:#eceae2; --muted:#9a978c;
    --line:#302f28; --line-soft:#26251f;
    --ok:#79c894; --ok-bg:#17281d;
    --bad:#f0836c; --bad-bg:#2c1815;
    --warn:#dfb45f; --warn-bg:#2a2113;
    --accent:#82b4ea; --accent-bg:#151f2b;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:26px;letter-spacing:-.02em;margin:0 0 4px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.09em;
  color:var(--muted);margin:44px 0 14px;font-weight:600}
h3{font-size:16px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13px;margin:0}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}

/* hero ------------------------------------------------------------------ */
.hero{border:1px solid var(--line);border-radius:12px;padding:26px 28px;
  margin-top:26px;background:var(--panel)}
.hero.pass{border-left:5px solid var(--ok)}
.hero.fail{border-left:5px solid var(--bad)}
.hero-label{font-size:12px;text-transform:uppercase;letter-spacing:.09em;
  color:var(--muted);font-weight:600}
.hero-fig{font-size:56px;font-weight:650;letter-spacing:-.03em;line-height:1.05;
  margin:8px 0 2px;font-family:var(--mono);font-variant-numeric:tabular-nums}
.pass .hero-fig{color:var(--ok)} .fail .hero-fig{color:var(--bad)}
.hero-note{color:var(--muted);font-size:13.5px;max-width:62ch;margin:10px 0 0}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.08em;
  padding:3px 9px;border-radius:5px;vertical-align:middle;margin-left:10px}
.badge.pass{background:var(--ok-bg);color:var(--ok)}
.badge.fail{background:var(--bad-bg);color:var(--bad)}

/* stat grid ------------------------------------------------------------- */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:12px;margin-top:12px}
.stat{border:1px solid var(--line);border-radius:10px;padding:16px 18px;
  background:var(--panel)}
.stat .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);font-weight:600}
.stat .v{font-size:27px;font-weight:600;letter-spacing:-.02em;margin-top:5px;
  font-family:var(--mono);font-variant-numeric:tabular-nums}
.stat .n{font-size:12px;color:var(--muted);margin-top:2px}

/* split bar ------------------------------------------------------------- */
.split{display:flex;height:10px;border-radius:5px;overflow:hidden;
  margin:14px 0 8px;background:var(--line-soft)}
.split i{display:block}
.legend{display:flex;gap:18px;font-size:12.5px;color:var(--muted);flex-wrap:wrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;
  margin-right:6px;vertical-align:baseline}

/* tables ---------------------------------------------------------------- */
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;
  background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);font-weight:600;padding:11px 14px;
  border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 14px;border-bottom:1px solid var(--line-soft);
  vertical-align:middle}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-family:var(--mono);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{position:relative;height:6px;background:var(--line-soft);border-radius:3px;
  width:110px;display:inline-block;vertical-align:middle}
.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:3px;
  background:var(--accent)}

/* exception cards -------------------------------------------------------- */
.exc{border:1px solid var(--line);border-left:4px solid var(--bad);
  border-radius:10px;padding:18px 20px;margin-bottom:12px;background:var(--panel)}
.exc-head{display:flex;justify-content:space-between;align-items:baseline;
  gap:16px;flex-wrap:wrap;margin-bottom:9px}
.exc-id{font-family:var(--mono);font-size:13px;font-weight:600}
.exc-amt{font-family:var(--mono);font-size:17px;font-weight:650;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;font-family:var(--mono);font-size:11px;
  padding:2px 8px;border-radius:4px;background:var(--bad-bg);color:var(--bad);
  margin-left:8px;font-weight:600}
.exc p{margin:0 0 9px;font-size:14px}
.act{background:var(--warn-bg);border-radius:7px;padding:10px 13px;
  font-size:13.5px}
.act b{color:var(--warn);font-size:11px;text-transform:uppercase;
  letter-spacing:.07em;display:block;margin-bottom:3px}
.flag{background:var(--accent-bg);border-radius:7px;padding:10px 13px;
  font-size:13px;margin-bottom:9px;color:var(--ink)}
.flag b{color:var(--accent)}

/* notes ------------------------------------------------------------------ */
.note{border:1px solid var(--line);border-radius:10px;padding:18px 20px;
  background:var(--panel);font-size:13.5px;color:var(--muted)}
.note strong{color:var(--ink)}
.note ul{margin:9px 0 0;padding-left:20px} .note li{margin-bottom:6px}
footer{margin-top:44px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12.5px}
a{color:var(--accent)}
@media print{
  body{background:#fff}
  .wrap{max-width:none;padding:0}
  .exc,.stat,.hero,.scroll,.note{break-inside:avoid}
  h2{margin-top:24px}
}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _pct(x, dash="—"):
    return dash if x is None else f"{x * 100:.1f}%"


def _rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


#: Reason codes whose defining feature is that every total balances. Worth
#: calling out on the page, because "delta Rs 0.00" next to "escalated" looks
#: like a bug until you know why it is the whole point.
_ZERO_DELTA_CLASSES = {AnomalyClass.LEDGER_MISMATCH}


def render_html(metrics: dict, findings: list, timing: dict,
                units: dict | None = None, agent: dict | None = None,
                dataset: str = "data") -> str:
    fc = metrics["false_clear_count"]
    passed = fc == 0
    exceptions = sorted(
        (f for f in findings if f.disposition is Disposition.EXCEPTION),
        key=lambda f: (-abs(f.delta), f.settlement_id))
    reconciled = metrics["reconciled"]
    total = metrics["settlements"]
    rate = timing["lines"] / timing["total_s"] if timing["total_s"] else 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    P = []
    A = P.append
    A(f'<title>Settlement Reconciliation Report</title><style>{CSS}</style>')
    A('<div class="wrap">')

    # -- header ---------------------------------------------------------
    A('<h1>Settlement reconciliation</h1>')
    A(f'<p class="sub">Three-way: PSP payout &middot; bank statement &middot; '
      f'order ledger &nbsp;|&nbsp; dataset <span class="mono">{_e(dataset)}</span>'
      f' &nbsp;|&nbsp; {now} &nbsp;|&nbsp; commit '
      f'<span class="mono">{_rev()}</span></p>')

    # -- hero: the safety metric, first and largest ----------------------
    A(f'<div class="hero {"pass" if passed else "fail"}">')
    A('<div class="hero-label">False-clear rate '
      f'<span class="badge {"pass" if passed else "fail"}">'
      f'{"PASS" if passed else "FAIL"}</span></div>')
    A(f'<div class="hero-fig">{_pct(metrics["false_clear_rate"])}</div>')
    A(f'<div class="sub mono">{fc} of {metrics["must_escalate_total"]} '
      f'must-escalate settlements were wrongly cleared</div>')
    A('<p class="hero-note">This is the expensive error. A missed exception is '
      'money quietly lost; an unnecessary one costs only review time. The two '
      'are not symmetric, so this number leads the report and the match rate '
      'follows it.</p>')
    A('</div>')

    # -- stat grid -------------------------------------------------------
    A('<h2>Run summary</h2><div class="stats">')
    for k, v, n in [
        ("Match rate", _pct(metrics["match_rate"]),
         f"{reconciled} of {total} auto-reconciled"),
        ("False escalates", str(metrics["false_escalate_count"]),
         "sent to a human unnecessarily"),
        ("Reason-code accuracy", _pct(metrics["classification_accuracy"]),
         "correct cause identified"),
        ("Throughput", f"{rate:,.0f}/s",
         f"{timing['lines']:,} lines in {timing['total_s']:.3f}s"),
    ]:
        A(f'<div class="stat"><div class="k">{k}</div>'
          f'<div class="v">{_e(v)}</div><div class="n">{_e(n)}</div></div>')
    A('</div>')

    # -- disposition split ----------------------------------------------
    rp = reconciled / total * 100 if total else 0
    A(f'<div class="split"><i style="width:{rp:.2f}%;background:var(--ok)"></i>'
      f'<i style="width:{100 - rp:.2f}%;background:var(--bad)"></i></div>')
    A(f'<div class="legend">'
      f'<span><span class="dot" style="background:var(--ok)"></span>'
      f'{reconciled} reconciled</span>'
      f'<span><span class="dot" style="background:var(--bad)"></span>'
      f'{total - reconciled} need a human</span>'
      f'<span>{timing["bank_rows"]:,} bank rows &middot; '
      f'{timing["orders"]:,} orders reconciled</span></div>')

    # -- exception list --------------------------------------------------
    A(f'<h2>Exception list &mdash; {len(exceptions)} need a human</h2>')
    if not exceptions:
        A('<div class="note">Nothing outstanding.</div>')
    for f in exceptions:
        A('<div class="exc">')
        A(f'<div class="exc-head"><span class="exc-id">{_e(f.settlement_id)}'
          f'<span class="tag">{_e(f.reason_code.value)}</span></span>'
          f'<span class="exc-amt">{_e(rupees(f.delta))}</span></div>')
        if f.reason_code in _ZERO_DELTA_CLASSES and f.delta == 0:
            A('<div class="flag"><b>Every total balances.</b> The payout ties '
              'to the bank credit to the paise. Only the order ledger '
              'disagrees &mdash; which is why a two-way reconciliation cannot '
              'see this at all.</div>')
        A(f'<p>{_e(f.explanation)}</p>')
        if f.action_required:
            A(f'<div class="act"><b>Action</b>{_e(f.action_required)}</div>')
        A('</div>')

    # -- per class -------------------------------------------------------
    A('<h2>Per class</h2><div class="scroll"><table>')
    A('<tr><th>Reason code</th><th class="num">n</th><th class="num">Precision</th>'
      '<th class="num">Recall</th><th>&nbsp;</th></tr>')
    for r in metrics["per_class"]:
        if r["support"] == 0 and r["fp"] == 0:
            continue
        w = (r["recall"] or 0) * 100
        A(f'<tr><td class="mono">{_e(r["class"])}</td>'
          f'<td class="num">{r["support"]}</td>'
          f'<td class="num">{_pct(r["precision"])}</td>'
          f'<td class="num">{_pct(r["recall"])}</td>'
          f'<td><span class="bar"><i style="width:{w:.1f}%"></i></span></td></tr>')
    A('</table></div>')

    # -- agent layer -----------------------------------------------------
    if agent:
        A('<h2>Agent layer</h2>')
        if agent.get("is_stub"):
            A('<div class="note"><strong>Scripted stub &mdash; not a real model '
              'call.</strong> This run drove the agent code path with a client '
              'that misbehaves on purpose, to exercise the guard offline. '
              'Nothing below measures model quality, and the token figures the '
              'stub reports are fabricated, so no cost is shown.</div>')
        g = agent["guard"]
        A('<div class="stats" style="margin-top:12px">')
        A(f'<div class="stat"><div class="k">Guard rejections</div>'
          f'<div class="v">{g["rejected"]}/{g["checked"]}</div>'
          f'<div class="n">{_pct(g["rejection_rate"])} of model outputs '
          f'overruled by arithmetic</div></div>')
        A(f'<div class="stat"><div class="k">Notes accepted</div>'
          f'<div class="v">{agent["narrations_accepted"]}</div>'
          f'<div class="n">passed every figure check</div></div>')
        A(f'<div class="stat"><div class="k">Matches accepted</div>'
          f'<div class="v">{agent["matches_accepted"]}</div>'
          f'<div class="n">re-verified against exact amount + date</div></div>')
        A('</div>')
        if g["reasons"]:
            A('<div class="scroll" style="margin-top:12px"><table>')
            A('<tr><th>Rejection reason</th><th class="num">n</th></tr>')
            for why, n in sorted(g["reasons"].items(), key=lambda kv: -kv[1]):
                A(f'<tr><td class="mono">{_e(why)}</td>'
                  f'<td class="num">{n}</td></tr>')
            A('</table></div>')

    # -- caveats ---------------------------------------------------------
    A('<h2>How to read this</h2><div class="note">')
    A('<strong>These are synthetic figures scored against a generated answer '
      'key.</strong> The key is committed alongside the data, so every number '
      'here is re-derivable &mdash; but the same author wrote the defect '
      'generator and the rules that detect them, which caps what a high score '
      'on this dataset can prove.')
    A('<ul>')
    A('<li><strong>Near-perfect per-class scores are not a result.</strong> '
      'They show two expressions of one set of assumptions agreeing with each '
      'other. The honest number is the adversarial holdout, which carries two '
      'defects per settlement and sits outside the classifier&rsquo;s design.</li>')
    A('<li><strong>Differences of 5 paise or less are absorbed</strong> as '
      'fee/GST rounding, so sub-rupee skimming is invisible to this system. '
      'Deliberate, and a real hole.</li>')
    A('<li><strong>Bank charges are identified by narration keywords.</strong> '
      'A bank wording its fees differently gets its shortfalls escalated as '
      'unexplained &mdash; safe, but noisy.</li>')
    A('<li><strong>A settlement with two defects gets one reason code.</strong> '
      'The disposition stays correct; the explanation is partial.</li>')
    A('</ul></div>')

    A(f'<footer>Generated by <span class="mono">python -m recon.cli</span> '
      f'&middot; Razorpay AI Buildathon, Track 04 &middot; full methodology and '
      f'caveats in <span class="mono">eval/metrics.md</span> and '
      f'<span class="mono">docs/FAILURE_LOG.md</span></footer>')
    A('</div>')
    return "\n".join(P)


def write_html(path: Path, metrics: dict, findings: list, timing: dict,
               units: dict | None = None, agent: dict | None = None,
               dataset: str = "data"):
    Path(path).write_text(
        render_html(metrics, findings, timing, units, agent, dataset),
        encoding="utf-8")
