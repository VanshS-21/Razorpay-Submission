"""Render a run as a single self-contained HTML page.

Design constraints, in order:

1. **The safety metric leads.** False-clear rate is the page's one loud move --
   a flooded plate carrying the figure at display scale. Match rate, the
   flattering number, comes after. A report that opens with good news and
   buries the dangerous one is doing the reader a disservice.
2. **Every exception carries its action.** An exception list without
   instructions is just a list of problems.
3. **No network.** The typeface is embedded as a data URI; there is no CDN, no
   webfont request, no external asset. The file opens from a fresh clone,
   offline, and looks identical on any machine.
4. **It prints.** Finance artefacts get printed and attached to emails.

Visual system: Swiss neo-grotesque on a near-white cool sheet -- an exposed
12-column hairline grid, one grotesk (Archivo), and exactly one signal ink.
The ink is red and it is spent entirely on risk: reconciled state gets no
colour at all. On this page "fine" is the absence of red, which is how a
control document should read.
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


@lru_cache(maxsize=1)
def _font_face() -> str:
    """Archivo (SIL Open Font License 1.1) inlined as a data URI.

    Embedded rather than linked so the report stays a single file that renders
    identically offline. Latin subset, variable 400-800: 35 KB.
    """
    f = _ASSETS / "archivo-latin.woff2"
    if not f.exists():                      # degrade to a grotesk stack
        return ""
    b64 = base64.b64encode(f.read_bytes()).decode("ascii")
    return (
        "@font-face{font-family:'Archivo';font-style:normal;"
        "font-weight:400 800;font-display:swap;"
        f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
    )


STAMP = """/* Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4
 * genre: editorial · macrostructure: Stat-Led · theme: Grid
 * paper: oklch(99% 0.003 255) · ink: oklch(16% 0.010 255)
 * signal ink: oklch(55% 0.21 28) red — one ink, spent entirely on risk;
 *   reconciled state carries no colour, so "fine" reads as the absence of red
 * display: Archivo 800 lowercase, embedded woff2 (no network)
 * axes: light paper · grotesk-heavy display · warm accent
 * enrichment: none — typography + constructed geometry (plate, settlement
 *   matrix, cropped numerals, stepped bars on real recall values)
 * nav: document masthead (page has no destinations) · footer: Ft4 colophon
 * tone: institutional · audience: engineers and finance reviewers
 */
"""

TOKENS = """
:root{
  /* paper: never #fff -- a faintly cool near-white sheet */
  --color-paper:oklch(99% 0.003 255);
  --color-paper-2:oklch(97.2% 0.004 255);
  --color-paper-3:oklch(94.5% 0.005 255);
  --color-ink:oklch(16% 0.010 255);
  --color-ink-2:oklch(38% 0.008 255);
  --color-muted:oklch(54% 0.006 255);
  --color-rule:oklch(88% 0.004 255);
  --color-rule-2:oklch(80% 0.005 255);

  /* exactly one signal ink. it means risk, and nothing else means anything. */
  --color-signal:oklch(55% 0.21 28);
  --color-signal-deep:oklch(46% 0.19 28);
  --color-focus:oklch(55% 0.21 28);

  --font-display:'Archivo',"Helvetica Neue",Helvetica,Arial,sans-serif;
  --font-body:'Archivo',"Helvetica Neue",Helvetica,Arial,sans-serif;
  --display-weight:800;
  --tracking-display:-0.045em;
  --tracking-label:0.09em;

  --text-display:clamp(46px,9vw,104px);
  --text-numeral:clamp(88px,17vw,232px);
  --text-2xl:clamp(28px,3.6vw,42px);
  --text-xl:22px; --text-lg:17px; --text-md:15px;
  --text-sm:13.5px; --text-xs:12px;

  --space-3xs:2px; --space-2xs:4px; --space-xs:8px; --space-sm:12px; --space-md:20px;
  --space-lg:32px; --space-xl:52px; --space-2xl:84px; --space-3xl:128px;

  --rule-hair:1px; --rule-solid:2px;
  --radius-card:0; --shadow-card:none;
  --dur-fast:180ms; --dur-mid:220ms;
  --ease-out:cubic-bezier(.22,.61,.36,1);
  --ease-in:cubic-bezier(.55,.06,.68,.19);
  --ease-in-out:cubic-bezier(.65,.05,.36,1);

  --shell:1180px; --col:calc(100% / 12);
}
"""

CSS = TOKENS + """
*{box-sizing:border-box}
html,body{overflow-x:clip;margin:0}
body{
  background:var(--color-paper);color:var(--color-ink);
  font-family:var(--font-body);font-size:var(--text-md);line-height:1.5;
  font-weight:400;-webkit-font-smoothing:antialiased;
  font-variant-numeric:tabular-nums;
}
::selection{background:var(--color-signal);color:var(--color-paper)}

/* the exposed 12-column grid: content, not scaffolding ------------------ */
.sheet{position:relative;max-width:var(--shell);margin:0 auto;
  padding:0 var(--space-lg)}
.sheet::before{
  content:"";position:absolute;inset:0;pointer-events-none;z-index:0;
  margin:0 var(--space-lg);
  background:repeating-linear-gradient(to right,
    var(--color-rule) 0 1px, transparent 1px var(--col));
  opacity:.62;
}
.band{position:relative;z-index:1;padding:var(--space-2xl) 0}
.band--tight{padding:var(--space-xl) 0}
.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));
  gap:var(--space-md)}

/* type ------------------------------------------------------------------ */
h1,h2,h3{font-family:var(--font-display);font-style:normal;
  font-weight:var(--display-weight);letter-spacing:var(--tracking-display);
  line-height:.92;margin:0;text-transform:lowercase;
  overflow-wrap:anywhere;min-width:0}
h1{font-size:var(--text-display)}
h2{font-size:var(--text-2xl)}
h3{font-size:var(--text-lg);letter-spacing:-.02em}
p{margin:0 0 var(--space-sm)}
.label{font-size:var(--text-xs);font-weight:600;text-transform:uppercase;
  letter-spacing:var(--tracking-label);color:var(--color-muted)}
.lede{font-size:var(--text-lg);line-height:1.45;color:var(--color-ink-2);
  max-width:58ch}
.note-body{font-size:var(--text-sm);color:var(--color-ink-2);max-width:62ch}
.dot-sq{display:inline-block;width:.52em;height:.52em;
  background:var(--color-signal);vertical-align:baseline}

/* masthead -------------------------------------------------------------- */
.mast{border-bottom:var(--rule-solid) solid var(--color-ink);
  padding:var(--space-lg) 0 var(--space-sm)}
.mast-sources{margin:var(--space-sm) 0 0}
.mast h1{font-size:clamp(34px,5.4vw,62px)}
.meta{border-top:var(--rule-hair) solid var(--color-rule);
  margin-top:var(--space-sm);padding-top:var(--space-xs);
  display:flex;flex-wrap:wrap;gap:var(--space-md)}
.meta span{white-space:nowrap}

/* the plate: one flooded band, the page's single loud move --------------- */
.plate{position:relative;overflow:clip;margin-top:var(--space-xl);
  background:var(--color-ink);color:var(--color-paper);
  padding:var(--space-lg) var(--space-lg) var(--space-2xl)}
.plate--fail{background:var(--color-signal)}
.plate::before{content:"";position:absolute;inset:0;pointer-events-none;
  background:repeating-linear-gradient(to right,
    color-mix(in oklab,var(--color-paper) 16%,transparent) 0 1px,
    transparent 1px var(--col));}
.plate-inner{position:relative;z-index:1;
  display:grid;grid-template-columns:repeat(12,minmax(0,1fr));
  gap:var(--space-md);align-items:end}
.plate .label{color:color-mix(in oklab,var(--color-paper) 72%,transparent)}
.plate-fig{grid-column:1 / span 6;font-family:var(--font-display);
  font-weight:var(--display-weight);font-size:var(--text-numeral);
  letter-spacing:-.05em;line-height:.82;margin:var(--space-xs) 0 0}
.plate-said{grid-column:8 / span 5;padding-bottom:var(--space-sm)}
.plate-said p{font-size:var(--text-sm);
  color:color-mix(in oklab,var(--color-paper) 78%,transparent);
  max-width:46ch;margin:var(--space-xs) 0 0}
.verdict{font-family:var(--font-display);font-weight:var(--display-weight);
  font-size:var(--text-xl);letter-spacing:-.02em}

/* figure cells: hairline-divided, one ruled object ---------------------- */
.cells{border-top:var(--rule-solid) solid var(--color-ink);
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}
.cell{padding:var(--space-md) var(--space-md) var(--space-lg) 0;
  border-inline-start:var(--rule-hair) solid var(--color-rule)}
.cell:first-child{border-inline-start:0}
.cell:not(:first-child){padding-inline-start:var(--space-md)}
.cell .v{font-family:var(--font-display);font-weight:var(--display-weight);
  font-size:clamp(30px,4.2vw,50px);letter-spacing:-.04em;line-height:1;
  margin:var(--space-xs) 0 var(--space-2xs)}
.cell .n{font-size:var(--text-xs);color:var(--color-muted)}

/* settlement matrix: one square per settlement -------------------------- */
.matrix{display:grid;grid-template-columns:repeat(auto-fill,minmax(11px,1fr));
  gap:var(--space-3xs);margin:var(--space-md) 0 var(--space-sm)}
.matrix i{display:block;aspect-ratio:1;background:var(--color-rule)}
.matrix i.x{background:var(--color-signal)}
.key{display:flex;align-items:center;gap:var(--space-lg);flex-wrap:wrap;font-size:var(--text-xs);
  color:var(--color-muted)}
.key b{display:inline-block;width:9px;height:9px;margin-inline-end:7px}

/* exception index: numbered rows riding the 12 columns ------------------ */
.exc{border-top:var(--rule-hair) solid var(--color-rule);
  display:grid;grid-template-columns:repeat(12,minmax(0,1fr));
  gap:var(--space-md);padding:var(--space-md) 0;
  transition:background var(--dur-fast) var(--ease-out)}
.exc:first-of-type{border-top:var(--rule-solid) solid var(--color-ink)}
.exc:hover{background:var(--color-paper-2)}
.exc-n{grid-column:1 / span 1;font-family:var(--font-display);
  font-weight:var(--display-weight);font-size:var(--text-xl);
  letter-spacing:-.03em;color:var(--color-muted);line-height:1}
.exc-main{grid-column:2 / span 7;min-width:0}
.exc-side{grid-column:9 / span 4;text-align:right;min-width:0}
.exc-id{font-size:var(--text-sm);font-weight:600;overflow-wrap:anywhere}
.exc-code{font-size:var(--text-xs);font-weight:600;text-transform:uppercase;
  letter-spacing:var(--tracking-label);color:var(--color-signal);
  margin-top:var(--space-2xs)}
.exc-amt{font-family:var(--font-display);font-weight:var(--display-weight);
  font-size:var(--text-xl);letter-spacing:-.03em;line-height:1.1;
  overflow-wrap:anywhere}
.exc p{font-size:var(--text-sm);color:var(--color-ink-2);margin:var(--space-sm) 0 0;
  max-width:62ch}
.balances{margin-top:var(--space-sm);padding-inline-start:var(--space-sm);
  border-inline-start:var(--rule-solid) solid var(--color-signal);
  font-size:var(--text-sm);max-width:62ch}
.act{margin-top:var(--space-sm);border-top:var(--rule-hair) solid var(--color-rule);
  padding-top:var(--space-xs);font-size:var(--text-sm);max-width:62ch}
.act .label{display:block;margin-bottom:2px}

/* tables ---------------------------------------------------------------- */
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:var(--text-sm)}
thead th{border-top:var(--rule-solid) solid var(--color-ink);
  border-bottom:var(--rule-hair) solid var(--color-rule);
  padding:var(--space-xs) var(--space-sm) var(--space-xs) 0;
  text-align:left;font-size:var(--text-xs);font-weight:600;
  text-transform:uppercase;letter-spacing:var(--tracking-label);
  color:var(--color-muted);white-space:nowrap}
tbody td{border-bottom:var(--rule-hair) solid var(--color-rule);
  padding:var(--space-xs) var(--space-sm) var(--space-xs) 0;vertical-align:middle}
tbody tr{transition:background var(--dur-fast) var(--ease-out)}
tbody tr:hover{background:var(--color-paper-2)}
th.num,td.num{text-align:right;white-space:nowrap;padding-inline-end:var(--space-md)}
/* stepped bars, snapped to a track, against real numbers */
.steps{display:flex;gap:var(--space-3xs);align-items:flex-end;height:14px;min-width:120px}
.steps i{display:block;width:9px;background:var(--color-paper-3)}
.steps i.on{background:var(--color-ink)}

/* notes ----------------------------------------------------------------- */
.notes{border-top:var(--rule-solid) solid var(--color-ink);
  display:grid;grid-template-columns:repeat(12,minmax(0,1fr));
  gap:var(--space-md)}
.note{grid-column:span 6;padding:var(--space-md) var(--space-md) var(--space-lg) 0;
  border-bottom:var(--rule-hair) solid var(--color-rule)}
.note .label{display:block;margin-bottom:var(--space-2xs)}
.note h3{margin-bottom:var(--space-2xs)}

/* colophon -------------------------------------------------------------- */
.colophon{border-top:var(--rule-solid) solid var(--color-ink);
  margin-top:var(--space-2xl);padding:var(--space-md) 0 var(--space-2xl);
  display:grid;grid-template-columns:repeat(12,minmax(0,1fr));
  gap:var(--space-md);font-size:var(--text-xs);color:var(--color-muted)}
.colophon div{grid-column:span 4}
.colophon b{display:block;color:var(--color-ink);font-weight:600;
  text-transform:uppercase;letter-spacing:var(--tracking-label);
  margin-bottom:var(--space-2xs)}

:focus-visible{outline:2px solid var(--color-focus);outline-offset:2px}

/* responsive: verified 320 / 375 / 414 / 768 ---------------------------- */
@media (max-width:900px){
  .cells{grid-template-columns:repeat(2,minmax(0,1fr))}
  .cell:nth-child(3){border-inline-start:0;padding-inline-start:0}
  .cell:nth-child(3),.cell:nth-child(4){
    border-top:var(--rule-hair) solid var(--color-rule)}
  .plate-fig{grid-column:1 / span 12}
  .plate-said{grid-column:1 / span 12;padding-bottom:0}
  .note{grid-column:span 12}
  .colophon div{grid-column:span 12;margin-bottom:var(--space-sm)}
}
@media (max-width:680px){
  .sheet::before{background:repeating-linear-gradient(to right,
    var(--color-rule) 0 1px, transparent 1px calc(100% / 4));opacity:.5}
  .band{padding:var(--space-xl) 0}
  .exc-n{grid-column:1 / span 12;font-size:var(--text-lg)}
  .exc-main{grid-column:1 / span 12}
  .exc-side{grid-column:1 / span 12;text-align:left;margin-top:var(--space-2xs)}
  .cells{grid-template-columns:minmax(0,1fr)}
  .cell{border-inline-start:0;padding-inline-start:0;
    border-top:var(--rule-hair) solid var(--color-rule)}
  .cell:first-child{border-top:0}
}
@media (prefers-reduced-motion:reduce){
  *{transition-duration:.01ms !important;animation-duration:.01ms !important}
}
@media print{
  .sheet::before{opacity:.35}
  .band{padding:var(--space-lg) 0;break-inside:avoid}
  .exc,.note,.cell{break-inside:avoid}
  .plate{background:var(--color-ink) !important;
    -webkit-print-color-adjust:exact;print-color-adjust:exact}
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


def _steps(value: float | None, n: int = 5) -> str:
    """A stepped-bar figure against a real number. Never decorative."""
    filled = 0 if value is None else round(value * n)
    return ('<span class="steps" aria-hidden="true">'
            + "".join(f'<i class="{"on" if i < filled else ""}" '
                      f'style="height:{4 + i * 2.4:.0f}px"></i>'
                      for i in range(n))
            + "</span>")


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
    exc_ids = {f.settlement_id for f in exceptions}

    P = []
    A = P.append
    A("<title>Settlement Reconciliation Report</title>")
    A(f"<style>{STAMP}{_font_face()}{CSS}</style>")
    A('<div class="sheet">')

    # -- masthead --------------------------------------------------------
    A('<header class="mast">')
    A('<h1>settlement<br>reconciliation</h1>')
    A('<p class="label mast-sources">three-way &middot; psp payout &middot; '
      'bank statement &middot; order ledger</p>')
    A('<div class="meta label">')
    A(f'<span>dataset {_e(dataset)}</span><span>{total} settlements</span>'
      f'<span>{timing["lines"]:,} lines</span><span>{now}</span>'
      f'<span>commit {_rev()}</span>')
    A('</div></header>')

    # -- the plate: the page's one loud move -----------------------------
    A(f'<section class="plate {"" if passed else "plate--fail"}">'
      '<div class="plate-inner">')
    A('<div style="grid-column:1 / span 12"><span class="label">'
      'false-clear rate</span></div>')
    A(f'<div class="plate-fig">{_pct(metrics["false_clear_rate"])}</div>')
    A('<div class="plate-said">')
    A(f'<div class="verdict">{"nothing wrongly cleared" if passed else "false clears present"}</div>')
    A(f'<p>{fc} of {metrics["must_escalate_total"]} settlements that required a '
      f'human were auto-reconciled. This is the expensive error &mdash; a missed '
      f'exception is money quietly lost, an unnecessary one costs only review '
      f'time. The two are not symmetric, so this figure leads and the match '
      f'rate follows it.</p>')
    A('</div></div></section>')

    # -- figures ---------------------------------------------------------
    A('<section class="band band--tight"><div class="cells">')
    for k, v, n in [
        ("match rate", _pct(metrics["match_rate"]),
         f"{reconciled} of {total} auto-reconciled"),
        ("false escalates", str(metrics["false_escalate_count"]),
         "sent to a human unnecessarily"),
        ("reason-code accuracy", _pct(metrics["classification_accuracy"]),
         "correct cause identified"),
        ("throughput", f"{rate:,.0f}/s",
         f"{timing['lines']:,} lines in {timing['total_s']:.3f}s"),
    ]:
        A(f'<div class="cell"><span class="label">{k}</span>'
          f'<div class="v">{_e(v)}</div><div class="n">{_e(n)}</div></div>')
    A('</div></section>')

    # -- settlement matrix ------------------------------------------------
    A('<section class="band band--tight"><div class="grid">')
    A('<div style="grid-column:1 / span 12">'
      '<h2>every settlement in the batch<span class="dot-sq" aria-hidden="true"></span></h2></div>')
    A('<div style="grid-column:1 / span 8">')
    A(f'<div class="matrix" role="img" aria-label="One square per settlement: {reconciled} reconciled, {total - reconciled} escalated to a human.">')
    for f in findings:
        A(f'<i class="{"x" if f.settlement_id in exc_ids else ""}"></i>')
    A('</div><div class="key">')
    A(f'<span><b style="background:var(--color-rule)"></b>'
      f'{reconciled} reconciled</span>'
      f'<span><b style="background:var(--color-signal)"></b>'
      f'{total - reconciled} need a human</span>')
    A('</div></div>')
    A('<div style="grid-column:9 / span 4"><p class="note-body">'
      f'One square per settlement, in batch order. '
      f'{timing["bank_rows"]:,} bank rows and {timing["orders"]:,} orders were '
      f'read to place them.</p></div>')
    A('</div></section>')

    # -- exception index --------------------------------------------------
    A('<section class="band"><div class="grid" style="margin-bottom:var(--space-md)">')
    A(f'<div style="grid-column:1 / span 7"><h2>exceptions</h2>'
      f'<p class="lede" style="margin-top:var(--space-sm)">'
      f'{len(exceptions)} settlements the engine refused to clear. Each names '
      f'the money, the evidence, and the next action.</p></div>')
    A('</div>')
    if not exceptions:
        A('<p class="note-body">Nothing outstanding.</p>')
    for i, f in enumerate(exceptions, 1):
        A('<article class="exc">')
        A(f'<div class="exc-n">{i:02d}</div>')
        A('<div class="exc-main">')
        A(f'<div class="exc-id">{_e(f.settlement_id)}</div>')
        A(f'<div class="exc-code">{_e(f.reason_code.value.replace("_", " "))}</div>')
        if f.reason_code in _ZERO_DELTA_CLASSES and f.delta == 0:
            A('<div class="balances"><strong>Every total balances.</strong> '
              'The payout ties to the bank credit to the paise. Only the order '
              'ledger disagrees &mdash; which is why a two-way reconciliation '
              'cannot see this at all.</div>')
        A(f'<p>{_e(f.explanation)}</p>')
        if f.action_required:
            A(f'<div class="act"><span class="label">action</span>'
              f'{_e(f.action_required)}</div>')
        A('</div>')
        A(f'<div class="exc-side"><div class="exc-amt">{_e(rupees(f.delta))}</div>'
          f'<div class="label" style="margin-top:var(--space-2xs)">'
          f'{"balanced" if f.delta == 0 else "difference"}</div></div>')
        A('</article>')
    A('</section>')

    # -- per class --------------------------------------------------------
    A('<section class="band band--tight"><div class="grid" '
      'style="margin-bottom:var(--space-md)">')
    A('<div style="grid-column:1 / span 7"><h2>by reason code</h2></div></div>')
    A('<div class="tablewrap"><table><thead><tr>'
      '<th>reason code</th><th class="num">n</th><th class="num">precision</th>'
      '<th class="num">recall</th><th>&nbsp;</th></tr></thead><tbody>')
    for r in metrics["per_class"]:
        if r["support"] == 0 and r["fp"] == 0:
            continue
        A(f'<tr><td>{_e(r["class"].replace("_", " "))}</td>'
          f'<td class="num">{r["support"]}</td>'
          f'<td class="num">{_pct(r["precision"])}</td>'
          f'<td class="num">{_pct(r["recall"])}</td>'
          f'<td>{_steps(r["recall"])}</td></tr>')
    A('</tbody></table></div></section>')

    # -- agent layer ------------------------------------------------------
    if agent:
        A('<section class="band band--tight"><div class="grid" '
          'style="margin-bottom:var(--space-md)">')
        A('<div style="grid-column:1 / span 7"><h2>agent layer</h2>')
        if agent.get("is_stub"):
            A('<p class="lede" style="margin-top:var(--space-sm)">'
              'Scripted stub &mdash; not a real model call. This run drove the '
              'agent code path with a client that misbehaves on purpose, to '
              'exercise the guard offline. Nothing here measures model quality, '
              'and the stub&rsquo;s token figures are fabricated, so no cost is '
              'shown.</p>')
        A('</div></div>')
        g = agent["guard"]
        A('<div class="cells" style="grid-template-columns:repeat(3,minmax(0,1fr))">')
        for k, v, n in [
            ("guard rejections", f'{g["rejected"]}/{g["checked"]}',
             f'{_pct(g["rejection_rate"])} of model outputs overruled by arithmetic'),
            ("notes accepted", str(agent["narrations_accepted"]),
             "passed every figure check"),
            ("matches accepted", str(agent["matches_accepted"]),
             "re-verified against exact amount and date"),
        ]:
            A(f'<div class="cell"><span class="label">{k}</span>'
              f'<div class="v">{_e(v)}</div><div class="n">{_e(n)}</div></div>')
        A('</div>')
        if g["reasons"]:
            A('<div class="tablewrap" style="margin-top:var(--space-lg)">'
              '<table><thead><tr><th>rejection reason</th>'
              '<th class="num">n</th></tr></thead><tbody>')
            for why, n in sorted(g["reasons"].items(), key=lambda kv: -kv[1]):
                A(f'<tr><td>{_e(why)}</td><td class="num">{n}</td></tr>')
            A('</tbody></table></div>')
        A('</section>')

    # -- reading notes ----------------------------------------------------
    A('<section class="band band--tight"><div class="grid" '
      'style="margin-bottom:var(--space-md)">')
    A('<div style="grid-column:1 / span 7"><h2>how to read this</h2>'
      '<p class="lede" style="margin-top:var(--space-sm)">Synthetic figures, '
      'scored against a generated answer key committed beside the data. Every '
      'number is re-derivable &mdash; but the same author wrote the defect '
      'generator and the rules that detect them, which caps what a high score '
      'here can prove.</p></div></div>')
    A('<div class="notes">')
    for i, (head, body) in enumerate([
        ("near-perfect scores are not a result",
         "They show two expressions of one set of assumptions agreeing with "
         "each other. The honest number is the adversarial holdout, which "
         "carries two defects per settlement and sits outside the "
         "classifier's design."),
        ("sub-rupee differences are invisible",
         "Anything at or under 5 paise is absorbed as fee and GST rounding, so "
         "this system cannot detect sub-rupee skimming. Deliberate, and a real "
         "hole."),
        ("bank charges are matched on words",
         "A shortfall is written off only when the statement itemises it, using "
         "a narrow keyword list. A bank wording its fees differently gets its "
         "shortfalls escalated as unexplained &mdash; safe, but noisy."),
        ("one reason code per settlement",
         "A settlement carrying two defects still gets a single label. The "
         "disposition stays correct; the explanation is partial."),
    ], 1):
        A(f'<div class="note"><span class="label">note {i:02d}</span>'
          f'<h3>{head}</h3><p class="note-body">{body}</p></div>')
    A('</div></section>')

    # -- colophon ---------------------------------------------------------
    A('<footer class="colophon">')
    A('<div><b>generated by</b>python -m recon.cli<br>'
      'Razorpay AI Buildathon &middot; Track 04</div>')
    A('<div><b>methodology</b>eval/metrics.md carries the full numbers and '
      'their caveats</div>')
    A('<div><b>what broke</b>docs/FAILURE_LOG.md, kept live rather than '
      'reconstructed</div>')
    A('</footer>')

    A('</div>')
    return "\n".join(P)


def write_html(path: Path, metrics: dict, findings: list, timing: dict,
               units: dict | None = None, agent: dict | None = None,
               dataset: str = "data"):
    Path(path).write_text(
        render_html(metrics, findings, timing, units, agent, dataset),
        encoding="utf-8")
