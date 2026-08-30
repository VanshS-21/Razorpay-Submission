"""Render a run as a single self-contained HTML page.

Design constraints, in order:

1. **The safety metric leads.** False-clear rate is the first row of the summary
   and carries the pass/fail mark. Match rate, the flattering number, comes
   after. A report that opens with good news and buries the dangerous one is
   doing the reader a disservice.
2. **Every exception carries its action.** An exception list without
   instructions is just a list of problems.
3. **No network.** The typefaces are embedded as data URIs; there is no CDN, no
   webfont request, no external asset. The file opens from a fresh clone,
   offline, and looks identical on any machine.
4. **It prints.** Finance artefacts get printed and attached to emails.

Visual register: a quiet institutional document. IBM Plex Sans for text, IBM
Plex Mono for every figure so rupee columns align down the page. Hairline rules
carry the structure, there is no display type and no flooded colour, and the
single accent is red -- spent only on money at risk. Reconciled state carries no
colour at all, so on this page "fine" reads as the absence of red.
"""

from __future__ import annotations

import base64
import html
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .models import AnomalyClass, Disposition, rupees

_ASSETS = Path(__file__).resolve().parent / "assets"

_FACES = [
    ("Plex Sans", "plex-sans.woff2", "400 600"),
    ("Plex Mono", "plex-mono-400.woff2", "400"),
    ("Plex Mono", "plex-mono-600.woff2", "600"),
]


@lru_cache(maxsize=1)
def _font_faces() -> str:
    """IBM Plex (SIL Open Font License 1.1) inlined as data URIs.

    Embedded rather than linked so the report stays one file that renders
    identically offline. Latin subsets only: ~76 KB of font in total.
    """
    out = []
    for family, fname, weight in _FACES:
        f = _ASSETS / fname
        if not f.exists():                  # degrade to the system stack
            continue
        b64 = base64.b64encode(f.read_bytes()).decode("ascii")
        out.append(
            f"@font-face{{font-family:'{family}';font-style:normal;"
            f"font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}")
    return "".join(out)


TOKENS = """
:root{
  --color-paper:oklch(99.2% 0.001 250);
  --color-paper-2:oklch(97.6% 0.002 250);
  --color-paper-3:oklch(95% 0.002 250);
  --color-ink:oklch(21% 0.006 250);
  --color-ink-2:oklch(40% 0.005 250);
  --color-muted:oklch(53% 0.004 250);
  --color-rule:oklch(89% 0.002 250);
  --color-rule-2:oklch(80% 0.003 250);

  /* one accent. it means money at risk, and nothing else means anything. */
  --color-signal:oklch(50% 0.19 27);
  --color-signal-soft:oklch(96% 0.02 27);
  --color-focus:oklch(45% 0.15 250);

  --font-body:'Plex Sans',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --font-mono:'Plex Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;

  --text-title:26px; --text-head:17px; --text-fig:30px;
  --text-md:14.5px; --text-sm:13px; --text-xs:11.5px;

  --space-3xs:2px; --space-2xs:4px; --space-xs:8px; --space-sm:12px;
  --space-md:20px; --space-lg:32px; --space-xl:48px; --space-2xl:72px;

  --rule-hair:1px; --rule-solid:2px;
  --dur-fast:150ms;
  --ease-out:cubic-bezier(.22,.61,.36,1);
  --shell:940px;
}
"""

CSS = TOKENS + """
*{box-sizing:border-box}
html,body{overflow-x:clip;margin:0}
body{
  background:var(--color-paper);color:var(--color-ink);
  font-family:var(--font-body);font-size:var(--text-md);line-height:1.55;
  -webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums;
}
::selection{background:var(--color-paper-3)}
.sheet{max-width:var(--shell);margin:0 auto;
  padding:var(--space-2xl) var(--space-lg)}
.mono{font-family:var(--font-mono);font-variant-numeric:tabular-nums}

/* headings: normal case, modest scale, no display type ------------------- */
h1,h2,h3{font-weight:600;margin:0;font-style:normal;
  overflow-wrap:anywhere;min-width:0}
h1{font-size:var(--text-title);letter-spacing:.01em;text-transform:uppercase}
h2{font-size:var(--text-head);letter-spacing:-.01em}
h3{font-size:var(--text-md)}
p{margin:0 0 var(--space-sm)}
.sub{color:var(--color-muted);font-size:var(--text-sm);margin:var(--space-xs) 0 0}
/* margin, not whitespace in content() -- a trailing space inside content is
   collapsed by the renderer and the separator ends up glued to the next item */
.sub span:not(:last-child)::after{content:"\\00b7";
  margin:0 var(--space-xs);color:var(--color-rule-2)}

/* masthead --------------------------------------------------------------- */
.mast{border-bottom:var(--rule-solid) solid var(--color-ink);
  padding-bottom:var(--space-sm)}

/* section heads ---------------------------------------------------------- */
.sec{margin-top:var(--space-2xl)}
.sec-head{display:block;border-bottom:var(--rule-hair) solid var(--color-rule-2);
  padding-bottom:var(--space-xs);margin-bottom:var(--space-md)}
.sec-head h2{display:inline}
.sec-head .count{color:var(--color-muted);font-size:var(--text-sm);
  margin-inline-start:var(--space-xs)}

/* summary: label left, figure right, rule between ------------------------ */
.summary{margin-top:var(--space-xl);
  border-top:var(--rule-hair) solid var(--color-rule)}
.row{display:flex;align-items:baseline;gap:var(--space-md);
  padding:var(--space-sm) 0;border-bottom:var(--rule-hair) solid var(--color-rule)}
.row-k{flex:1 1 auto;min-width:0}
.row-k b{font-weight:600;display:block}
.row-k small{color:var(--color-muted);font-size:var(--text-sm);display:block;
  margin-top:1px}
.row-v{flex:0 0 auto;font-family:var(--font-mono);font-weight:600;
  font-size:var(--text-fig);letter-spacing:-.02em;line-height:1.1;
  text-align:right;white-space:nowrap}
.row--lead{border-top:var(--rule-solid) solid var(--color-ink);
  border-bottom:var(--rule-solid) solid var(--color-ink);
  padding:var(--space-md) 0}
.mark{font-family:var(--font-body);font-size:var(--text-xs);font-weight:600;
  letter-spacing:.08em;text-transform:uppercase;
  padding:3px var(--space-xs);margin-inline-start:var(--space-sm);
  border:var(--rule-hair) solid currentColor;vertical-align:middle}
.mark--pass{color:var(--color-ink-2)}
.mark--fail{color:var(--color-signal)}
.lead-note{color:var(--color-muted);font-size:var(--text-sm);
  margin:var(--space-sm) 0 0;max-width:70ch}

/* settlement matrix: small, quiet, factual ------------------------------- */
.matrix{display:grid;grid-template-columns:repeat(auto-fill,minmax(9px,1fr));
  gap:var(--space-3xs);margin-bottom:var(--space-sm)}
.matrix i{display:block;aspect-ratio:1;background:var(--color-rule-2)}
.matrix i.x{background:var(--color-signal)}
.key{display:flex;align-items:center;gap:var(--space-md);flex-wrap:wrap;
  font-size:var(--text-sm);color:var(--color-muted)}
.key b{display:inline-block;width:8px;height:8px;margin-inline-end:6px}

/* exceptions ------------------------------------------------------------- */
.exc{border-bottom:var(--rule-hair) solid var(--color-rule);
  padding:var(--space-md) 0;
  transition:background var(--dur-fast) var(--ease-out)}
.exc:first-of-type{border-top:var(--rule-hair) solid var(--color-rule)}
.exc:hover{background:var(--color-paper-2)}
.exc-top{display:flex;justify-content:space-between;align-items:baseline;
  gap:var(--space-md);flex-wrap:wrap}
.exc-id{font-family:var(--font-mono);font-size:var(--text-sm);font-weight:600;
  overflow-wrap:anywhere}
.exc-amt{font-family:var(--font-mono);font-weight:600;font-size:var(--text-head);
  color:var(--color-signal);white-space:nowrap}
.exc-amt.zero{color:var(--color-ink-2)}
.exc-code{color:var(--color-muted);font-size:var(--text-sm);
  margin-top:var(--space-3xs)}
.exc p{font-size:var(--text-sm);color:var(--color-ink-2);
  margin:var(--space-xs) 0 0;max-width:72ch}
.balances{margin-top:var(--space-xs);background:var(--color-signal-soft);
  padding:var(--space-xs) var(--space-sm);font-size:var(--text-sm);
  max-width:72ch}
.act{margin-top:var(--space-xs);font-size:var(--text-sm);max-width:72ch;
  display:flex;gap:var(--space-xs)}
.act::before{content:"\\2192";color:var(--color-muted);flex:0 0 auto}

/* tables ----------------------------------------------------------------- */
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:var(--text-sm)}
thead th{border-bottom:var(--rule-hair) solid var(--color-rule-2);
  padding:var(--space-xs) var(--space-sm) var(--space-xs) 0;text-align:left;
  font-size:var(--text-xs);font-weight:600;text-transform:uppercase;
  letter-spacing:.07em;color:var(--color-muted);white-space:nowrap}
tbody td{border-bottom:var(--rule-hair) solid var(--color-rule);
  padding:var(--space-xs) var(--space-sm) var(--space-xs) 0}
tbody tr{transition:background var(--dur-fast) var(--ease-out)}
tbody tr:hover{background:var(--color-paper-2)}
th.num,td.num{text-align:right;white-space:nowrap;
  padding-inline-end:var(--space-md);font-family:var(--font-mono)}
.track{display:block;height:4px;background:var(--color-paper-3);min-width:90px}
.track i{display:block;height:100%;background:var(--color-rule-2)}

/* notes ------------------------------------------------------------------ */
.notes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:var(--space-md) var(--space-lg)}
.note{border-top:var(--rule-hair) solid var(--color-rule);
  padding-top:var(--space-sm)}
.note p{font-size:var(--text-sm);color:var(--color-ink-2);
  margin:var(--space-2xs) 0 0}

/* colophon --------------------------------------------------------------- */
.colophon{margin-top:var(--space-2xl);
  border-top:var(--rule-solid) solid var(--color-ink);
  padding-top:var(--space-sm);display:flex;flex-wrap:wrap;
  gap:var(--space-md) var(--space-xl);font-size:var(--text-xs);
  color:var(--color-muted)}
.colophon b{color:var(--color-ink);font-weight:600;display:block;
  text-transform:uppercase;letter-spacing:.07em;margin-bottom:2px}

:focus-visible{outline:2px solid var(--color-focus);outline-offset:2px}

@media (max-width:680px){
  .sheet{padding:var(--space-xl) var(--space-md)}
  .row{flex-wrap:wrap;gap:var(--space-2xs)}
  .row-v{flex:1 1 100%;text-align:left}
  .notes{grid-template-columns:minmax(0,1fr)}
  :root{--text-fig:24px;--text-title:21px}
}
@media (prefers-reduced-motion:reduce){
  *{transition-duration:.01ms !important;animation-duration:.01ms !important}
}
@media print{
  .sheet{padding:0;max-width:none}
  .exc,.note,.row{break-inside:avoid}
  .exc:hover,tbody tr:hover{background:transparent}
}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _pct(x, dash="&mdash;"):
    return dash if x is None else f"{x * 100:.1f}%"


def _rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


#: Reason codes whose defining feature is that every total balances. Called out
#: on the page, because "Rs 0.00" beside "needs a human" reads as a bug until
#: you know it is the entire point of holding a third source.
_ZERO_DELTA_CLASSES = {AnomalyClass.LEDGER_MISMATCH}


def render_html(metrics: dict, findings: list, timing: dict,
                units: dict | None = None, agent: dict | None = None,
                dataset: str = "data", match_result=None) -> str:
    fc = metrics["false_clear_count"]
    passed = fc == 0
    exceptions = sorted(
        (f for f in findings if f.disposition is Disposition.EXCEPTION),
        key=lambda f: (-abs(f.delta), f.settlement_id))
    reconciled = metrics["reconciled"]
    total = metrics["settlements"]
    rate = timing["lines"] / timing["total_s"] if timing["total_s"] else 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    exc_ids = {f.settlement_id for f in exceptions}

    P = []
    A = P.append
    A("<title>Settlement Reconciliation Report</title>")
    A(f"<style>{_font_faces()}{CSS}</style>")
    A('<div class="sheet">')

    # -- masthead --------------------------------------------------------
    A('<header class="mast">')
    A("<h1>Settlement reconciliation</h1>")
    A('<p class="sub">')
    A(f"<span>Three-way: PSP payout, bank statement, order ledger</span>"
      f"<span>{total} settlements</span><span>{timing['lines']:,} lines</span>"
      f'<span class="mono">{_e(dataset)}</span><span>{now}</span>'
      f'<span class="mono">{_rev()}</span>')
    A("</p></header>")

    # -- summary: the safety figure first --------------------------------
    A('<div class="summary">')
    A('<div class="row row--lead"><div class="row-k"><b>False-clear rate'
      f'<span class="mark mark--{"pass" if passed else "fail"}">'
      f'{"pass" if passed else "fail"}</span></b>'
      f"<small>{fc} of {metrics['must_escalate_total']} settlements that "
      f"required a human were auto-reconciled</small></div>"
      f'<div class="row-v">{_pct(metrics["false_clear_rate"])}</div></div>')
    for k, sub, v in [
        ("Match rate", f"{reconciled} of {total} auto-reconciled",
         _pct(metrics["match_rate"])),
        ("False escalates", "sent to a human unnecessarily",
         str(metrics["false_escalate_count"])),
        ("Reason-code accuracy", "correct cause identified",
         _pct(metrics["classification_accuracy"])),
        ("Throughput", f"{timing['lines']:,} lines in {timing['total_s']:.3f}s",
         f"{rate:,.0f}/s"),
    ]:
        A(f'<div class="row"><div class="row-k"><b>{k}</b>'
          f"<small>{_e(sub)}</small></div>"
          f'<div class="row-v">{_e(v)}</div></div>')
    A("</div>")
    A('<p class="lead-note">The false-clear rate is listed first because it is '
      "the expensive error. A missed exception is money quietly lost; an "
      "unnecessary one costs only review time. The two are not symmetric.</p>")

    # -- settlement matrix ------------------------------------------------
    A('<section class="sec"><div class="sec-head"><h2>Batch</h2>'
      '<span class="count">one square per settlement</span></div>')
    A(f'<div class="matrix" role="img" aria-label="One square per settlement: '
      f'{reconciled} reconciled, {total - reconciled} escalated to a human.">')
    for f in findings:
        A(f'<i class="{"x" if f.settlement_id in exc_ids else ""}"></i>')
    A('</div><div class="key">')
    A(f'<span><b style="background:var(--color-rule-2)"></b>'
      f"{reconciled} reconciled</span>"
      f'<span><b style="background:var(--color-signal)"></b>'
      f"{total - reconciled} need a human</span>"
      f"<span>{timing['bank_rows']:,} bank rows, "
      f"{timing['orders']:,} orders read</span>")
    A("</div></section>")

    # -- bank-side coverage ------------------------------------------------
    # The statement is the second of three sources. Reporting only what
    # fraction of PAYOUTS reconciled, and never what fraction of the STATEMENT
    # was explained, leaves half the job unaccounted for.
    if match_result is not None:
        rows = list(match_result.orphan_bank)
        A('<section class="sec"><div class="sec-head">'
          "<h2>Bank-side coverage</h2>"
          f'<span class="count">{len(rows)} statement rows left '
          f"unexplained</span></div>")
        A('<div class="rows">')
        for k, sub_, v in [
            ("Unmatched credits", "money in, not tied to any payout",
             rupees(sum(r.credit for r in rows))),
            ("Unmatched debits", "money out, not tied to any payout",
             rupees(sum(r.debit for r in rows))),
        ]:
            A(f'<div class="row"><div class="row-k"><b>{k}</b>'
              f"<small>{_e(sub_)}</small></div>"
              f'<div class="row-v">{_e(v)}</div></div>')
        A("</div>")
        A('<p class="sub">Most of these are unrelated account traffic: payroll,'
          " other payment gateways, vendor payments. The engine does not claim"
          " to know which is which. It reports them so that nothing leaves the"
          " account unseen.</p>")
        A("</section>")

    # -- reconciled by inference -------------------------------------------
    inferred = [f for f in findings if f.resolved_by == "deterministic:inferred"]
    if inferred:
        A('<section class="sec"><div class="sec-head">'
          "<h2>Spot check</h2>"
          f'<span class="count">{len(inferred)} reconciled by inference, '
          f"not by evidence</span></div>")
        A('<p class="sub">A consolidated transfer is cleared because the nets'
          " add up, not because the statement says so \u2014 the bank quoted one"
          " UTR and the rest is inferred. Almost always right, and listed here"
          " because it is the one clear in this engine that no document"
          " corroborates.</p>")
        A('<div class="rows">')
        for f in inferred:
            A('<div class="row"><div class="row-k">'
              f"<b>{_e(f.settlement_id)}</b>"
              f'<small>{_e(f.reason_code.value.replace("_", " "))}</small></div>'
              f'<div class="row-v">{f.confidence:.2f}</div></div>')
        A("</div></section>")

    # -- exceptions --------------------------------------------------------
    A('<section class="sec"><div class="sec-head"><h2>Exceptions</h2>'
      f'<span class="count">{len(exceptions)} settlements the engine refused '
      f"to clear</span></div>")
    if not exceptions:
        A('<p class="sub">Nothing outstanding.</p>')
    for f in exceptions:
        A('<article class="exc"><div class="exc-top">')
        A(f'<div><div class="exc-id">{_e(f.settlement_id)}</div>'
          f'<div class="exc-code">'
          f'{_e(f.reason_code.value.replace("_", " ").capitalize())}</div></div>')
        A(f'<div class="exc-amt{" zero" if f.delta == 0 else ""}">'
          f"{_e(rupees(f.delta))}</div>")
        A("</div>")
        if f.reason_code in _ZERO_DELTA_CLASSES and f.delta == 0:
            A('<div class="balances"><strong>Every total balances.</strong> '
              "The payout ties to the bank credit to the paise. Only the order "
              "ledger disagrees &mdash; which is why a two-way reconciliation "
              "cannot see this at all.</div>")
        A(f"<p>{_e(f.explanation)}</p>")
        if f.action_required:
            A(f'<div class="act"><span>{_e(f.action_required)}</span></div>')
        A("</article>")
    A("</section>")

    # -- per class ---------------------------------------------------------
    A('<section class="sec"><div class="sec-head"><h2>By reason code</h2></div>')
    A('<div class="tablewrap"><table><thead><tr>'
      '<th>Reason code</th><th class="num">n</th><th class="num">Precision</th>'
      '<th class="num">Recall</th><th>&nbsp;</th></tr></thead><tbody>')
    for r in metrics["per_class"]:
        if r["support"] == 0 and r["fp"] == 0:
            continue
        w = (r["recall"] or 0) * 100
        A(f'<tr><td>{_e(r["class"].replace("_", " "))}</td>'
          f'<td class="num">{r["support"]}</td>'
          f'<td class="num">{_pct(r["precision"])}</td>'
          f'<td class="num">{_pct(r["recall"])}</td>'
          f'<td><span class="track" aria-hidden="true">'
          f'<i style="width:{w:.1f}%"></i></span></td></tr>')
    A("</tbody></table></div></section>")

    # -- agent layer -------------------------------------------------------
    if agent:
        A('<section class="sec"><div class="sec-head"><h2>Agent layer</h2></div>')
        if agent.get("is_stub"):
            A('<p class="lead-note" style="margin-top:0">'
              "<strong>Scripted stub &mdash; not a real model call.</strong> "
              "This run drove the agent code path with a client that misbehaves "
              "on purpose, to exercise the guard offline. Nothing here measures "
              "model quality, and the stub&rsquo;s token figures are fabricated, "
              "so no cost is shown.</p>")
        g = agent["guard"]
        A('<div class="summary" style="margin-top:var(--space-md)">')
        for k, sub, v in [
            ("Guard rejections",
             f'{_pct(g["rejection_rate"])} of model outputs overruled by arithmetic',
             f'{g["rejected"]}/{g["checked"]}'),
            ("Notes accepted", "passed every figure check",
             str(agent["narrations_accepted"])),
            ("Identity leads", "advisory only; never auto-cleared a payout",
             str(agent["matches_proposed"])),
        ]:
            A(f'<div class="row"><div class="row-k"><b>{k}</b>'
              f"<small>{_e(sub)}</small></div>"
              f'<div class="row-v">{_e(v)}</div></div>')
        A("</div>")
        if g["reasons"]:
            A('<div class="tablewrap" style="margin-top:var(--space-md)">'
              "<table><thead><tr><th>Rejection reason</th>"
              '<th class="num">n</th></tr></thead><tbody>')
            for why, n in sorted(g["reasons"].items(), key=lambda kv: -kv[1]):
                A(f'<tr><td class="mono">{_e(why)}</td>'
                  f'<td class="num">{n}</td></tr>')
            A("</tbody></table></div>")
        A("</section>")

    # -- reading notes -----------------------------------------------------
    A('<section class="sec"><div class="sec-head"><h2>How to read this</h2>'
      '<span class="count">limits of the figures above</span></div>')
    A('<p class="lead-note" style="margin:0 0 var(--space-md)">Synthetic '
      "figures, scored against a generated answer key committed beside the "
      "data. Every number is re-derivable &mdash; but the same author wrote the "
      "defect generator and the rules that detect them, which caps what a high "
      "score here can prove.</p>")
    A('<div class="notes">')
    for head, body in [
        ("Near-perfect scores are not a result",
         "They show two expressions of one set of assumptions agreeing with "
         "each other. The honest number is the adversarial holdout, which "
         "carries two defects per settlement and sits outside the "
         "classifier&rsquo;s design."),
        ("Sub-rupee differences are invisible",
         "Anything at or under 5 paise is absorbed as fee and GST rounding, so "
         "this system cannot detect sub-rupee skimming. Deliberate, and a real "
         "hole."),
        ("Bank charges are matched on words",
         "A shortfall is written off only when the statement itemises it, using "
         "a narrow keyword list. A bank wording its fees differently gets its "
         "shortfalls escalated as unexplained &mdash; safe, but noisy."),
        ("One reason code per settlement",
         "A settlement carrying two defects still gets a single label. The "
         "disposition stays correct; the explanation is partial."),
    ]:
        A(f'<div class="note"><h3>{head}</h3><p>{body}</p></div>')
    A("</div></section>")

    # -- colophon ----------------------------------------------------------
    A('<footer class="colophon">')
    A('<div><b>Generated by</b><span class="mono">python -m recon.cli</span></div>')
    A("<div><b>Methodology</b>eval/metrics.md</div>")
    A("<div><b>What broke</b>docs/FAILURE_LOG.md</div>")
    A("<div><b>Submission</b>Razorpay AI Buildathon, Track 04</div>")
    A("</footer>")

    A("</div>")
    return "\n".join(P)


def write_html(path: Path, metrics: dict, findings: list, timing: dict,
               units: dict | None = None, agent: dict | None = None,
               dataset: str = "data", match_result=None):
    Path(path).write_text(
        render_html(metrics, findings, timing, units, agent, dataset,
                    match_result),
        encoding="utf-8")
