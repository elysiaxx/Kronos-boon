"""
Shared chrome for the HTML reports: design tokens, layout CSS, and a small
SVG charting layer.

Both :mod:`mt5_backtest.report_html` (backtest accuracy) and
:mod:`mt5_backtest.live_report` (forward-test P&L) are built from the pieces
here, so the two pages look like one tool and a fix to an axis or a tooltip
lands in both.

The charting layer is deliberately tiny and dependency-free - plain SVG built
in the browser - so a report file can be mailed around, opened offline, and
still work. It exposes one global, ``Viz``.
"""

from __future__ import annotations

import json
import math

# --------------------------------------------------------------------------- #
# CSS
# --------------------------------------------------------------------------- #

BASE_CSS = r"""
  :root {
    color-scheme: light;
    --page:           #f9f9f7;
    --surface:        #fcfcfb;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --grid:           #e1e0d9;
    --axis:           #c3c2b7;
    --border:         rgba(11,11,11,0.10);
    --series-1:       #2a78d6;
    --series-2:       #eb6834;
    --good:           #0ca30c;
    --warning:        #fab219;
    --critical:       #d03b3b;
    --good-ink:       #006300;
    --wash:           rgba(42,120,214,0.08);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --page:           #0d0d0d;
      --surface:        #1a1a19;
      --text-primary:   #f0efec;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --grid:           #2c2c2a;
      --axis:           #383835;
      --border:         rgba(255,255,255,0.10);
      --series-1:       #3987e5;
      --series-2:       #d95926;
      --good-ink:       #0ca30c;
      --wash:           rgba(57,135,229,0.12);
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --page:           #0d0d0d;
    --surface:        #1a1a19;
    --text-primary:   #f0efec;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --grid:           #2c2c2a;
    --axis:           #383835;
    --border:         rgba(255,255,255,0.10);
    --series-1:       #3987e5;
    --series-2:       #d95926;
    --good-ink:       #0ca30c;
    --wash:           rgba(57,135,229,0.12);
  }

  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0 16px 64px;
    background: var(--page); color: var(--text-primary);
    font: 15px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 1040px; margin: 0 auto; }
  header { padding: 32px 0 8px; }
  h1 { font-size: 24px; line-height: 1.25; margin: 0 0 6px; }
  h2 { font-size: 18px; margin: 40px 0 4px; }
  h2 .num { color: var(--text-muted); font-weight: 400; margin-right: 6px; }
  .sub { color: var(--text-secondary); font-size: 13px; margin: 0; }
  .meta { color: var(--text-muted); font-size: 12.5px; margin-top: 10px; }
  .meta code { font-size: 12px; background: var(--surface); border: 1px solid var(--border);
               border-radius: 4px; padding: 1px 5px; }

  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 10px; padding: 16px 18px; margin-top: 14px; }

  .verdict { display: flex; gap: 14px; align-items: flex-start; }
  .verdict .dot { width: 12px; height: 12px; border-radius: 50%; margin-top: 7px; flex: none; }
  .verdict.good     .dot { background: var(--good); }
  .verdict.warning  .dot { background: var(--warning); }
  .verdict.critical .dot { background: var(--critical); }
  .verdict.unknown  .dot { background: var(--text-muted); }
  .verdict .headline { font-size: 17px; font-weight: 600; margin: 0 0 4px; }
  .verdict .body { color: var(--text-secondary); font-size: 13.5px; margin: 0; }

  .tiles { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); margin-top: 14px; }
  .tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .tile .label { font-size: 12px; color: var(--text-secondary); }
  .tile .label .en { color: var(--text-muted); }
  .tile .value { font-size: 26px; font-weight: 650; margin: 4px 0 2px; letter-spacing: -0.01em; }
  .tile .value.up { color: var(--good-ink); } .tile .value.down { color: var(--critical); }
  .tile .note { font-size: 12px; color: var(--text-muted); line-height: 1.45; }

  .chart { width: 100%; }
  .chart svg { display: block; width: 100%; height: auto; overflow: visible; }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px;
            color: var(--text-secondary); margin: 2px 0 10px; }
  .legend .k { display: inline-flex; align-items: center; gap: 6px; }
  .legend .sw { width: 14px; height: 3px; border-radius: 2px; flex: none; }

  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 4px 0 12px; }
  .controls button {
    font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 7px; cursor: pointer;
    background: var(--surface); color: var(--text-primary); border: 1px solid var(--border);
  }
  .controls button:hover:not(:disabled) { background: var(--wash); }
  .controls button:disabled { opacity: 0.4; cursor: default; }
  .controls input[type=range] { flex: 1 1 180px; min-width: 140px; accent-color: var(--series-1); }
  .controls .pos { font-size: 13px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }

  .facts { display: grid; gap: 10px 20px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           font-size: 13px; margin-top: 10px; padding-top: 12px; border-top: 1px solid var(--border); }
  .facts div span { display: block; color: var(--text-muted); font-size: 11.5px; }
  .facts b { font-weight: 600; font-variant-numeric: tabular-nums; }

  .badge { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600;
           padding: 1px 7px; border-radius: 20px; }
  .badge.hit,  .badge.long  { color: var(--good-ink); background: rgba(12,163,12,0.12); }
  .badge.miss, .badge.short { color: var(--critical); background: rgba(208,59,59,0.12); }

  table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
  th, td { text-align: right; padding: 7px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; }
  th { color: var(--text-secondary); font-weight: 600; font-size: 12px; position: sticky; top: 0;
       background: var(--surface); }
  tbody tr:hover { background: var(--wash); }
  .scroll { max-height: 420px; overflow: auto; border: 1px solid var(--border); border-radius: 10px;
            background: var(--surface); }
  td.up b, b.up { color: var(--good-ink); } td.down b, b.down { color: var(--critical); }

  details { margin-top: 10px; }
  details summary { cursor: pointer; font-size: 13px; color: var(--text-secondary); }
  dl.gloss { margin: 10px 0 0; font-size: 13.5px; }
  dl.gloss dt { font-weight: 600; margin-top: 12px; }
  dl.gloss dd { margin: 2px 0 0; color: var(--text-secondary); }
  .tip { position: fixed; pointer-events: none; z-index: 9; opacity: 0; transition: opacity .08s;
         background: var(--surface); color: var(--text-primary); border: 1px solid var(--border);
         border-radius: 8px; padding: 7px 10px; font-size: 12.5px; line-height: 1.5;
         box-shadow: 0 6px 20px rgba(0,0,0,0.14); font-variant-numeric: tabular-nums; max-width: 280px; }
  .tip b { font-weight: 600; }
  .caveat { font-size: 12.5px; color: var(--text-muted); margin-top: 10px; }
  .caveat.warn { color: var(--critical); }
  @media (max-width: 560px) { h1 { font-size: 20px; } .tile .value { font-size: 22px; } }
"""

# --------------------------------------------------------------------------- #
# JS charting layer
# --------------------------------------------------------------------------- #

CHART_JS = r"""
window.Viz = (function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const tip = document.getElementById("tip");

  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const fmt = (v, d) => (v === null || v === undefined || Number.isNaN(v)) ? "–"
        : (v === 0 ? 0 : v).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d) => (v === null || v === undefined || Number.isNaN(v)) ? "–"
        : fmt(v * 100, d === undefined ? 1 : d) + "%";
  const signed = (v, d) => (v === null || v === undefined || Number.isNaN(v)) ? "–"
        : (v >= 0 ? "+" : "") + fmt(v, d);
  const shortTs = (s) => String(s).replace("T", " ").slice(5, 16);

  function mk(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function showTip(evt, html) {
    if (!tip) return;
    tip.innerHTML = html;
    tip.style.opacity = 1;
    const r = tip.getBoundingClientRect();
    let x = evt.clientX + 14, y = evt.clientY - r.height - 10;
    if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - 14;
    if (y < 8) y = evt.clientY + 18;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  const hideTip = () => { if (tip) tip.style.opacity = 0; };

  function ticks(lo, hi, n) {
    if (!(hi > lo)) return [lo];
    const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
    const out = [];
    for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) out.push(t);
    return out;
  }
  function frame(host, h, pad) {
    host.innerHTML = "";
    const w = Math.max(320, host.clientWidth || 640);
    const svg = mk("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h,
                            role: "img", preserveAspectRatio: "none" }, host);
    return { svg, w, h, pad, iw: w - pad.l - pad.r, ih: h - pad.t - pad.b };
  }
  function yAxis(f, lo, hi, label, dec) {
    const { svg, pad, iw, ih } = f;
    const Y = (v) => pad.t + ih - (v - lo) / (hi - lo || 1) * ih;
    for (const t of ticks(lo, hi, 5)) {
      const y = Y(t);
      if (y < pad.t - 1 || y > pad.t + ih + 1) continue;
      mk("line", { x1: pad.l, x2: pad.l + iw, y1: y, y2: y, stroke: css("--grid"), "stroke-width": 1 }, svg);
      const tx = mk("text", { x: pad.l - 8, y: y + 4, "text-anchor": "end", fill: css("--text-muted"),
                              "font-size": 11, "font-variant-numeric": "tabular-nums" }, svg);
      tx.textContent = fmt(t, dec === undefined ? 0 : dec);
    }
    if (label) {
      const tx = mk("text", { x: pad.l, y: 12, fill: css("--text-muted"), "font-size": 11 }, svg);
      tx.textContent = label;
    }
    return Y;
  }
  function xLabels(f, items) {
    for (const it of items) {
      const tx = mk("text", { x: it.x, y: f.pad.t + f.ih + 18, "text-anchor": "middle",
                              fill: css("--text-muted"), "font-size": 11,
                              "font-variant-numeric": "tabular-nums" }, f.svg);
      tx.textContent = it.text;
    }
  }
  function xTitle(f, text) {
    if (!text) return;
    const tx = mk("text", { x: f.pad.l + f.iw / 2, y: f.h - 2, "text-anchor": "middle",
                            fill: css("--text-muted"), "font-size": 11 }, f.svg);
    tx.textContent = text;
  }
  function baseline(f, y) {
    mk("line", { x1: f.pad.l, x2: f.pad.l + f.iw, y1: y, y2: y, stroke: css("--axis"), "stroke-width": 1 }, f.svg);
  }
  function endLabel(f, x, y, color, text) {
    const tx = mk("text", { x: x + 9, y: y + 4, fill: color, "font-size": 11.5, "font-weight": 600,
                            "font-variant-numeric": "tabular-nums" }, f.svg);
    tx.textContent = text;
    return tx;
  }

  /* Bars, always read from zero. opts:
     {height, padRight, yLabel, dec, xTitle, max, ref, refLabel:[l1,l2], colorFor(row)} */
  function barChart(hostId, rows, opts) {
    opts = opts || {};
    const host = document.getElementById(hostId);
    if (!host || !rows.length) return;
    const f = frame(host, opts.height || 240, { l: 58, r: opts.padRight || 14, t: 20, b: 34 });
    const vals = rows.map(r => r.v);
    const lo = Math.min(0, ...vals);
    const hi = opts.max !== undefined ? opts.max
             : Math.max(...vals, opts.ref === undefined ? -Infinity : opts.ref) * 1.12;
    const Y = yAxis(f, lo, hi, opts.yLabel, opts.dec);
    const bw = f.iw / rows.length;
    const gap = Math.min(6, Math.max(2, bw * 0.22));   // >= 2px surface gap between bars
    const w0 = Math.max(2, bw - gap);
    const y0 = Y(0);
    const every = Math.max(1, Math.round(rows.length / 12));
    rows.forEach((r, k) => {
      const x = f.pad.l + k * bw + gap / 2;
      const y = Y(r.v);
      const top = Math.min(y, y0), h = Math.max(1, Math.abs(y0 - y));
      const rad = Math.min(4, w0 / 2, h);
      const up = r.v >= 0;
      // rounded data-end, square against the baseline
      const d = up
        ? `M${x} ${y0} V${top + rad} Q${x} ${top} ${x + rad} ${top} H${x + w0 - rad}`
          + ` Q${x + w0} ${top} ${x + w0} ${top + rad} V${y0} Z`
        : `M${x} ${y0} V${top + h - rad} Q${x} ${top + h} ${x + rad} ${top + h} H${x + w0 - rad}`
          + ` Q${x + w0} ${top + h} ${x + w0} ${top + h - rad} V${y0} Z`;
      const bar = mk("path", { d, fill: opts.colorFor ? opts.colorFor(r) : css("--series-1") }, f.svg);
      if (r.tip) {
        bar.addEventListener("mousemove", (e) => showTip(e, r.tip));
        bar.addEventListener("mouseleave", hideTip);
      }
      const isLast = k === rows.length - 1;
      if (k % every === 0 || (isLast && (k % every) > every / 2))
        xLabels(f, [{ x: x + w0 / 2, text: r.x }]);
    });
    baseline(f, y0);
    if (opts.ref !== undefined) {
      const y = Y(opts.ref);
      mk("line", { x1: f.pad.l, x2: f.pad.l + f.iw, y1: y, y2: y, stroke: css("--critical"),
                   "stroke-width": 2, "stroke-dasharray": "5 4" }, f.svg);
      (opts.refLabel || []).forEach((line, li) => {
        const t = mk("text", { x: f.pad.l + f.iw + 8,
                               y: y + 4 + (li - (opts.refLabel.length - 1) / 2) * 14,
                               fill: css("--critical"), "font-size": 11,
                               "font-weight": li === 0 ? 700 : 500 }, f.svg);
        t.textContent = line;
      });
    }
    xTitle(f, opts.xTitle);
    return f;
  }

  /* Lines in data space. opts:
     {height, padRight, yLabel, yDec, xTitle, xTicks:[{v,text}], hline, vline:{v,label},
      series:[{pts:[[x,y]..], color, dash, endLabel, endDot}], snap:[x..], tooltip(x)} */
  function lineChart(hostId, opts) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const all = opts.series.flatMap(s => s.pts);
    if (!all.length) { host.innerHTML = ""; return; }
    const f = frame(host, opts.height || 260, { l: 58, r: opts.padRight || 14, t: 20, b: 34 });
    const xs = all.map(p => p[0]), ys = all.map(p => p[1]);
    const x0 = opts.xDomain ? opts.xDomain[0] : Math.min(...xs);
    const x1 = opts.xDomain ? opts.xDomain[1] : Math.max(...xs);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (opts.hline !== undefined) { lo = Math.min(lo, opts.hline); hi = Math.max(hi, opts.hline); }
    const padv = (hi - lo || 1) * 0.1; lo -= padv; hi += padv;
    const X = (v) => f.pad.l + (x1 === x0 ? 0 : (v - x0) / (x1 - x0) * f.iw);
    const Y = yAxis(f, lo, hi, opts.yLabel, opts.yDec);
    baseline(f, f.pad.t + f.ih);
    if (opts.hline !== undefined)
      mk("line", { x1: f.pad.l, x2: f.pad.l + f.iw, y1: Y(opts.hline), y2: Y(opts.hline),
                   stroke: css("--axis"), "stroke-width": 1 }, f.svg);
    if (opts.vline) {
      const x = X(opts.vline.v);
      mk("line", { x1: x, x2: x, y1: f.pad.t, y2: f.pad.t + f.ih, stroke: css("--axis"),
                   "stroke-width": 1, "stroke-dasharray": "3 3" }, f.svg);
      if (opts.vline.label) {
        const t = mk("text", { x: x + 5, y: f.pad.t + 11, fill: css("--text-muted"), "font-size": 11 }, f.svg);
        t.textContent = opts.vline.label;
      }
    }
    const ends = [];
    for (const s of opts.series) {
      if (!s.pts.length) continue;
      const d = s.pts.map((p, k) => (k ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join(" ");
      mk("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round",
                   "stroke-linecap": "round", ...(s.dash ? { "stroke-dasharray": s.dash } : {}) }, f.svg);
      const last = s.pts[s.pts.length - 1];
      if (s.endDot !== false)
        mk("circle", { cx: X(last[0]), cy: Y(last[1]), r: 4.5, fill: s.color,
                       stroke: css("--surface"), "stroke-width": 2 }, f.svg);
      if (s.endLabel) ends.push({ x: X(last[0]), y: Y(last[1]), color: s.color, text: s.endLabel });
    }
    // nudge colliding end labels apart so both stay readable
    if (ends.length === 2 && Math.abs(ends[0].y - ends[1].y) < 14) {
      const mid = (ends[0].y + ends[1].y) / 2;
      const up = ends[0].y <= ends[1].y ? 0 : 1;
      ends[up].y = mid - 7; ends[1 - up].y = mid + 7;
    }
    for (const e of ends) endLabel(f, e.x, e.y, e.color, e.text);
    if (opts.xTicks) xLabels(f, opts.xTicks.map(t => ({ x: X(t.v), text: t.text })));
    xTitle(f, opts.xTitle);

    if (opts.tooltip && opts.snap && opts.snap.length) {
      const cross = mk("line", { y1: f.pad.t, y2: f.pad.t + f.ih, stroke: css("--axis"),
                                 "stroke-width": 1, opacity: 0 }, f.svg);
      const hit = mk("rect", { x: f.pad.l, y: f.pad.t, width: f.iw, height: f.ih, fill: "transparent" }, f.svg);
      hit.addEventListener("mousemove", (e) => {
        const bb = f.svg.getBoundingClientRect();
        const px = (e.clientX - bb.left) / bb.width * f.w;
        let best = opts.snap[0], bd = Infinity;
        for (const v of opts.snap) {
          const d = Math.abs(X(v) - px);
          if (d < bd) { bd = d; best = v; }
        }
        cross.setAttribute("x1", X(best)); cross.setAttribute("x2", X(best));
        cross.setAttribute("opacity", 1);
        const html = opts.tooltip(best);
        if (html) showTip(e, html); else hideTip();
      });
      hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); hideTip(); });
    }
    return f;
  }

  /* Evenly spaced tick positions over an index range, always including the last. */
  function indexTicks(n, want) {
    const step = Math.max(1, Math.round((n - 1) / (want || 5)));
    const out = [];
    for (let k = 0; k < n; k += step) out.push({ v: k, text: String(k + 1) });
    if (!out.length || out[out.length - 1].v !== n - 1) out.push({ v: n - 1, text: String(n) });
    return out;
  }

  function onResize(fn) {
    let t;
    addEventListener("resize", () => { clearTimeout(t); t = setTimeout(fn, 120); });
  }

  return { css, fmt, pct, signed, shortTs, mk, showTip, hideTip, ticks, frame, yAxis,
           xLabels, xTitle, baseline, endLabel, barChart, lineChart, indexTicks, onResize };
})();
"""

_PAGE = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<div class="wrap">
__BODY__
</div>
<div class="tip" id="tip"></div>
<script id="payload" type="application/json">__DATA__</script>
<script>__CHART_JS__</script>
<script>__PAGE_JS__</script>
</body>
</html>
"""


def jsonable(o):
    """JSON fallback for types the encoder cannot handle (numpy scalars/arrays)."""
    try:
        import numpy as np
        if isinstance(o, np.generic):
            value = o.item()
            return None if isinstance(value, float) and not math.isfinite(value) else value
        if isinstance(o, np.ndarray):
            return finite_only(o.tolist())
    except Exception:
        pass
    return str(o)


def finite_only(o):
    """Replace every non-finite number with None, recursively.

    NaN and Infinity are not JSON. ``json.dumps`` writes them as bare ``NaN`` /
    ``Infinity`` tokens, which Python reads back happily and every browser's
    JSON.parse rejects - so a page whose payload holds one dies on its first
    statement and renders nothing but its server-rendered shell. A ``default=``
    hook cannot save it either: ``default`` is only consulted for types the
    encoder cannot serialize, and a float always can be. The scrub has to
    happen before serialization.

    Statistics go non-finite in ordinary states, not exotic ones: profit factor
    with no losing trade yet, a bootstrap interval under two trades, a mean of
    an empty list. Those are exactly the early runs a report is most useful for.
    """
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: finite_only(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [finite_only(v) for v in o]
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return finite_only(o.item())
        if isinstance(o, np.ndarray):
            return finite_only(o.tolist())
    except Exception:
        pass
    return o


def embed_json(data) -> str:
    """Serialize for a <script type="application/json"> block."""
    # allow_nan=False is the backstop: if anything non-finite survives the
    # scrub, fail loudly here rather than ship a page that cannot parse.
    # Guard against an early </script> closing the block from inside a string.
    return json.dumps(finite_only(data), default=jsonable, ensure_ascii=False,
                      allow_nan=False).replace("</", "<\\/")


def page(title: str, body: str, data, page_js: str) -> str:
    """Assemble one standalone report page."""
    html = _PAGE
    for key, value in (
        ("__TITLE__", title),
        ("__CSS__", BASE_CSS),
        ("__BODY__", body),
        ("__DATA__", embed_json(data)),
        ("__CHART_JS__", CHART_JS),
        ("__PAGE_JS__", page_js),
    ):
        html = html.replace(key, value)
    return html
