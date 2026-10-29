"""
Render a walk-forward backtest run as a single self-contained HTML report.

The text report that :mod:`mt5_backtest.backtest` prints is dense: it answers
"how accurate was it" with numbers only. This module turns the same run into a
page you can click through - every forecast window drawn against what actually
happened, each metric spelled out in plain language, and a table of all
windows - so the result is readable without knowing what MAE or Sharpe mean.

Two ways in:

    # as part of a run (backtest.py does this automatically)
    from mt5_backtest.report_html import write_report
    write_report(detail, "results/XAUUSD_M15_..._report.html")

    # or rebuild the page later from a saved *_detail.json, no model needed
    python -m mt5_backtest.report_html results/XAUUSD_M15_..._detail.json

The output embeds its own data and draws its own SVG charts, so it needs no
network access and no plotting library - just a browser.
"""

from __future__ import annotations

import argparse
import json
import math
import os


# --------------------------------------------------------------------------- #
# detail payload
# --------------------------------------------------------------------------- #

def build_detail(summary: dict, windows: list[dict], symbol: str, timeframe: str) -> dict:
    """Bundle everything the HTML page needs into one JSON-safe dict."""
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "summary": summary,
        "windows": windows,
    }


def _verdict(detail: dict) -> dict:
    """Pre-compute the plain-language verdict so the page has no logic to guess."""
    s = detail["summary"]
    ds = s.get("directional_stats") or {}
    n = ds.get("n") or s.get("n_windows") or 0
    hits = ds.get("hits")
    acc = s.get("directional_accuracy_at_eval_horizon")
    p = ds.get("p_value_vs_coinflip")

    if not n or acc is None:
        return {"level": "unknown", "headline": "Không đủ dữ liệu để kết luận.", "detail": ""}

    pct = acc * 100.0
    lo = (ds.get("ci95_low") or 0.0) * 100.0
    hi = (ds.get("ci95_high") or 0.0) * 100.0

    if p is not None and p < 0.05 and pct > 50:
        level = "good"
        headline = f"Đoán đúng hướng {pct:.0f}% - tốt hơn tung đồng xu một cách có ý nghĩa thống kê."
    elif p is not None and p < 0.05 and pct < 50:
        level = "critical"
        headline = f"Đoán đúng hướng chỉ {pct:.0f}% - tệ hơn tung đồng xu một cách có ý nghĩa thống kê."
    else:
        level = "warning"
        headline = f"Đoán đúng hướng {pct:.0f}% - CHƯA đủ bằng chứng là hơn tung đồng xu (50%)."

    detail_txt = (
        f"Đúng {hits}/{n} lần dự báo. Khoảng tin cậy 95% là {lo:.0f}%–{hi:.0f}%, "
        f"tức con số thật có thể nằm bất kỳ đâu trong khoảng đó. "
        f"p = {p:.2f}" + (" (< 0.05: khó xảy ra do may mắn)." if (p is not None and p < 0.05)
                          else " (> 0.05: kết quả này hoàn toàn có thể chỉ do may mắn).")
    )
    if 50.0 in (lo, hi) or lo < 50.0 < hi:
        detail_txt += " Vì khoảng tin cậy vẫn trùm qua mốc 50%, cần nhiều cửa sổ hơn để kết luận."
    return {"level": level, "headline": headline, "detail": detail_txt}


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

_TEMPLATE = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
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
  .verdict.good    .dot { background: var(--good); }
  .verdict.warning .dot { background: var(--warning); }
  .verdict.critical .dot { background: var(--critical); }
  .verdict.unknown .dot { background: var(--text-muted); }
  .verdict .headline { font-size: 17px; font-weight: 600; margin: 0 0 4px; }
  .verdict .body { color: var(--text-secondary); font-size: 13.5px; margin: 0; }

  .tiles { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); margin-top: 14px; }
  .tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .tile .label { font-size: 12px; color: var(--text-secondary); text-transform: none; }
  .tile .label .en { color: var(--text-muted); }
  .tile .value { font-size: 26px; font-weight: 650; margin: 4px 0 2px; letter-spacing: -0.01em; }
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
  .badge.hit  { color: var(--good-ink);   background: rgba(12,163,12,0.12); }
  .badge.miss { color: var(--critical);   background: rgba(208,59,59,0.12); }

  table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
  th, td { text-align: right; padding: 7px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; }
  th { color: var(--text-secondary); font-weight: 600; font-size: 12px; position: sticky; top: 0;
       background: var(--surface); }
  tbody tr:hover { background: var(--wash); }
  .scroll { max-height: 420px; overflow: auto; border: 1px solid var(--border); border-radius: 10px;
            background: var(--surface); }
  td.up b { color: var(--good-ink); } td.down b { color: var(--critical); }

  details { margin-top: 10px; }
  details summary { cursor: pointer; font-size: 13px; color: var(--text-secondary); }
  dl.gloss { margin: 10px 0 0; font-size: 13.5px; }
  dl.gloss dt { font-weight: 600; margin-top: 12px; }
  dl.gloss dd { margin: 2px 0 0; color: var(--text-secondary); }
  .tip { position: fixed; pointer-events: none; z-index: 9; opacity: 0; transition: opacity .08s;
         background: var(--surface); color: var(--text-primary); border: 1px solid var(--border);
         border-radius: 8px; padding: 7px 10px; font-size: 12.5px; line-height: 1.5;
         box-shadow: 0 6px 20px rgba(0,0,0,0.14); font-variant-numeric: tabular-nums; max-width: 260px; }
  .tip b { font-weight: 600; }
  .caveat { font-size: 12.5px; color: var(--text-muted); margin-top: 10px; }
  @media (max-width: 560px) { h1 { font-size: 20px; } .tile .value { font-size: 22px; } }
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>__TITLE__</h1>
  <p class="sub">Kronos dự báo <b>__PRED_LEN__</b> nến tới từ <b>__LOOKBACK__</b> nến quá khứ, lặp lại <b>__N_WINDOWS__</b> lần trên dữ liệu đã biết kết quả. Mỗi lần dự báo chỉ thấy quá khứ, nên không có chuyện &ldquo;nhìn trước đáp án&rdquo;.</p>
  <p class="meta">__META__</p>
</header>

<div class="card verdict __VERDICT_LEVEL__">
  <div class="dot"></div>
  <div>
    <p class="headline">__VERDICT_HEADLINE__</p>
    <p class="body">__VERDICT_DETAIL__</p>
  </div>
</div>

<div class="tiles" id="tiles"></div>

<h2><span class="num">1.</span>Từng lần dự báo trông thế nào?</h2>
<p class="sub">Đường cam là dự báo, đường xanh là giá thật xảy ra sau đó. Kéo thanh trượt để xem lần lượt __N_WINDOWS__ cửa sổ.</p>
<div class="card">
  <div class="controls">
    <button id="prev">&larr; Trước</button>
    <button id="next">Sau &rarr;</button>
    <input type="range" id="slider" min="0" value="0">
    <span class="pos" id="pos"></span>
    <button id="worst">Lần tệ nhất</button>
    <button id="best">Lần tốt nhất</button>
  </div>
  <div class="legend">
    <span class="k"><span class="sw" style="background:var(--text-muted)"></span>Quá khứ mô hình được thấy</span>
    <span class="k"><span class="sw" style="background:var(--series-1)"></span>Giá thật (actual)</span>
    <span class="k"><span class="sw" style="background:var(--series-2)"></span>Kronos dự báo (forecast)</span>
  </div>
  <div class="chart" id="windowChart"></div>
  <div class="facts" id="windowFacts"></div>
</div>

<h2><span class="num">2.</span>Dự báo càng xa thì càng kém?</h2>
<p class="sub">Mỗi cột là một mốc &ldquo;bao nhiêu nến phía trước&rdquo;. Cột cao hơn vạch 50% nghĩa là ở mốc đó mô hình đoán hướng tốt hơn tung đồng xu.</p>
<div class="card">
  <div class="chart" id="dirChart"></div>
  <p class="caveat">Mỗi cột chỉ dựa trên __N_WINDOWS__ mẫu, nên dao động lên xuống giữa các cột phần lớn là nhiễu. Hãy xem xu hướng chung, đừng bắt một cột lẻ.</p>
</div>
<div class="card">
  <p class="sub" style="margin-bottom:8px">Sai số giá trung bình (MAE) theo tầm xa &mdash; dự báo lệch bao nhiêu đơn vị giá.</p>
  <div class="chart" id="maeChart"></div>
</div>
<details>
  <summary>Xem dạng bảng</summary>
  <div class="scroll" style="margin-top:10px"><table id="horizonTable"></table></div>
</details>

<h2><span class="num">3.</span>Nếu giao dịch mù theo tín hiệu thì sao?</h2>
<p class="sub">Mỗi cửa sổ: mô hình đoán lên thì mua, đoán xuống thì bán, giữ đúng __PRED_LEN__ nến rồi đóng. Đường dưới là tổng lãi/lỗ cộng dồn, tính theo % giá.</p>
<div class="card">
  <div class="chart" id="equityChart"></div>
  <p class="caveat">Đây là phép thử thô để biến &ldquo;dự báo có dùng được không&rdquo; thành một con số: không spread, không slippage, không quản lý vốn, không cắt lỗ, mỗi lệnh cùng khối lượng. Không phải khuyến nghị đầu tư.</p>
</div>

<h2><span class="num">4.</span>Bảng chi tiết từng cửa sổ</h2>
<p class="sub">Một dòng là một lần dự báo. Bấm vào dòng để xem biểu đồ của cửa sổ đó ở mục 1.</p>
<div class="card" style="padding:0; overflow:hidden">
  <div class="scroll" style="border:0"><table id="windowTable"></table></div>
</div>

<h2><span class="num">5.</span>Các chỉ số nghĩa là gì</h2>
<div class="card">
  <dl class="gloss">
    <dt>Directional accuracy &mdash; đoán đúng hướng</dt>
    <dd>Tỷ lệ số lần mô hình đoán đúng là giá sẽ cao hơn hay thấp hơn giá hiện tại. 50% = tung đồng xu. Đây là chỉ số quan trọng nhất với giao dịch: đoán sai hướng thì sai số giá nhỏ cũng vô dụng.</dd>
    <dt>MAE &mdash; sai số tuyệt đối trung bình</dt>
    <dd>Trung bình dự báo lệch bao nhiêu đơn vị giá so với thực tế. Với XAUUSD, MAE = 13 nghĩa là lệch khoảng 13 USD/ounce.</dd>
    <dt>RMSE</dt>
    <dd>Giống MAE nhưng phạt nặng các lần lệch lớn. RMSE cao hơn MAE nhiều = có vài lần dự báo sai rất xa.</dd>
    <dt>MAPE</dt>
    <dd>Sai số tính theo phần trăm giá, nên so sánh được giữa các sản phẩm có giá khác nhau.</dd>
    <dt>Khoảng tin cậy 95% (CI) &amp; p-value</dt>
    <dd>Với số cửa sổ ít, một tỷ lệ đúng cao có thể chỉ do may. CI cho biết con số thật có thể nằm trong khoảng nào; p-value là xác suất thấy kết quả lệch khỏi 50/50 như vậy nếu mô hình thực chất chỉ đoán bừa. p &lt; 0.05 mới coi là có bằng chứng.</dd>
    <dt>Hit rate &amp; Sharpe mỗi lệnh</dt>
    <dd>Hit rate là tỷ lệ lệnh có lãi. Sharpe mỗi lệnh = lãi trung bình chia độ lệch chuẩn: đo lãi so với mức dao động. Càng cao càng ổn định; dưới ~0.2 là rất mong manh.</dd>
  </dl>
</div>

</div>
<div class="tip" id="tip"></div>

<script id="payload" type="application/json">__DATA__</script>
<script>
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("payload").textContent);
  const S = D.summary, W = D.windows, CFG = S.config || {};
  const NS = "http://www.w3.org/2000/svg";
  const tip = document.getElementById("tip");

  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const fmt = (v, d) => (v === null || v === undefined || Number.isNaN(v)) ? "–"
        : (v === 0 ? 0 : v).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : fmt(v * 100, d === undefined ? 1 : d) + "%";
  const signed = (v, d) => (v >= 0 ? "+" : "") + fmt(v, d);
  const shortTs = (s) => String(s).replace("T", " ").slice(5, 16);

  function mk(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function showTip(evt, html) {
    tip.innerHTML = html;
    tip.style.opacity = 1;
    const r = tip.getBoundingClientRect();
    let x = evt.clientX + 14, y = evt.clientY - r.height - 10;
    if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - 14;
    if (y < 8) y = evt.clientY + 18;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  const hideTip = () => { tip.style.opacity = 0; };

  // ---- generic axes -------------------------------------------------------
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
  function xLabels(f, items) { // items: [{x, text}]
    const { svg, pad, ih } = f;
    for (const it of items) {
      const tx = mk("text", { x: it.x, y: pad.t + ih + 18, "text-anchor": "middle",
                              fill: css("--text-muted"), "font-size": 11,
                              "font-variant-numeric": "tabular-nums" }, svg);
      tx.textContent = it.text;
    }
  }
  function baseline(f, y) {
    mk("line", { x1: f.pad.l, x2: f.pad.l + f.iw, y1: y, y2: y, stroke: css("--axis"), "stroke-width": 1 }, f.svg);
  }

  // ---- stat tiles ---------------------------------------------------------
  function tiles() {
    const pe = S.point_errors_overall || {}, st = S.naive_strategy || {}, ds = S.directional_stats || {};
    const acc = S.directional_accuracy_at_eval_horizon;
    const list = [
      { label: "Đoán đúng hướng", en: "directional accuracy",
        value: pct(acc, 0),
        note: `Đúng ${ds.hits ?? "–"}/${ds.n ?? S.n_windows} lần, ở mốc ${S.eval_horizon} nến phía trước. 50% = tung đồng xu.` },
      { label: "Khoảng tin cậy 95%", en: "confidence interval",
        value: `${pct(ds.ci95_low, 0)} – ${pct(ds.ci95_high, 0)}`,
        note: `p = ${fmt(ds.p_value_vs_coinflip, 2)} so với 50/50. ` +
              ((ds.p_value_vs_coinflip < 0.05) ? "Khó là may mắn." : "Chưa loại được khả năng may mắn.") },
      { label: "Sai số giá trung bình", en: "MAE",
        value: fmt(pe.mae, 2),
        note: `Lệch ~${fmt(pe.mape_pct, 2)}% giá. RMSE ${fmt(pe.rmse, 2)} (lệch lớn nhất bị phạt nặng hơn).` },
      { label: "Lãi cộng dồn (mô phỏng thô)", en: "naive total return",
        value: signed(st.total_return_pct, 2) + "%",
        note: `${st.n_trades} lệnh, hit rate ${pct(st.hit_rate, 0)}, trung bình ${signed(st.avg_return_pct, 3)}%/lệnh.` },
      { label: "Sharpe mỗi lệnh", en: "sharpe per trade",
        value: fmt(st.sharpe_per_trade, 2),
        note: "Lãi trung bình chia độ dao động. Dưới ~0.2 là mong manh, chưa trừ phí." },
    ];
    document.getElementById("tiles").innerHTML = list.map(t => `
      <div class="tile">
        <div class="label">${t.label} <span class="en">(${t.en})</span></div>
        <div class="value">${t.value}</div>
        <div class="note">${t.note}</div>
      </div>`).join("");
  }

  // ---- 1. window explorer -------------------------------------------------
  const histTail = (w) => w.hist_close.length;
  let cur = 0;

  function drawWindow(i) {
    const w = W[i], host = document.getElementById("windowChart");
    const f = frame(host, 320, { l: 58, r: 104, t: 20, b: 34 });
    const nh = w.hist_close.length, nf = w.fut_close.length;
    const all = w.hist_close.concat(w.fut_close, w.pred_close);
    let lo = Math.min(...all), hi = Math.max(...all);
    const padv = (hi - lo || 1) * 0.08; lo -= padv; hi += padv;

    const X = (k) => f.pad.l + (nh + nf <= 1 ? 0 : k / (nh + nf - 1) * f.iw);
    const Y = yAxis(f, lo, hi, "giá (close)", 0);
    baseline(f, f.pad.t + f.ih);

    // the "now" divider: everything left of it is what the model saw
    const xNow = X(nh - 1);
    mk("line", { x1: xNow, x2: xNow, y1: f.pad.t, y2: f.pad.t + f.ih,
                 stroke: css("--axis"), "stroke-width": 1, "stroke-dasharray": "3 3" }, f.svg);
    const lab = mk("text", { x: xNow + 5, y: f.pad.t + 11, fill: css("--text-muted"), "font-size": 11 }, f.svg);
    lab.textContent = "thời điểm dự báo";

    const path = (pts, color, dash) => {
      const d = pts.map((p, k) => (k ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
      mk("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round",
                   "stroke-linecap": "round", ...(dash ? { "stroke-dasharray": dash } : {}) }, f.svg);
    };
    path(w.hist_close.map((v, k) => [X(k), Y(v)]), css("--text-muted"));
    // future paths start at the anchor so the join is visible
    const a = [X(nh - 1), Y(w.anchor)];
    path([a].concat(w.fut_close.map((v, k) => [X(nh + k), Y(v)])), css("--series-1"));
    path([a].concat(w.pred_close.map((v, k) => [X(nh + k), Y(v)])), css("--series-2"), "6 4");

    // end markers + direct labels (so identity is never colour-alone)
    const endA = [X(nh + nf - 1), Y(w.fut_close[nf - 1])];
    const endP = [X(nh + nf - 1), Y(w.pred_close[nf - 1])];
    for (const [p, c] of [[endA, css("--series-1")], [endP, css("--series-2")]]) {
      mk("circle", { cx: p[0], cy: p[1], r: 4.5, fill: c, stroke: css("--surface"), "stroke-width": 2 }, f.svg);
    }
    let yA = endA[1], yP = endP[1];
    if (Math.abs(yA - yP) < 14) {           // keep the two labels legible
      const mid = (yA + yP) / 2;
      yA = mid + (yA >= yP ? 7 : -7);
      yP = mid + (yP > yA ? 7 : -7);
    }
    const dl = (x, y, c, t) => {
      const tx = mk("text", { x: x + 9, y: y + 4, fill: c, "font-size": 11.5, "font-weight": 600,
                              "font-variant-numeric": "tabular-nums" }, f.svg);
      tx.textContent = t;
    };
    dl(endA[0], yA, css("--series-1"), "thật " + fmt(w.fut_close[nf - 1], 1));
    dl(endP[0], yP, css("--series-2"), "dự báo " + fmt(w.pred_close[nf - 1], 1));

    // crosshair + tooltip
    const cross = mk("line", { y1: f.pad.t, y2: f.pad.t + f.ih, stroke: css("--axis"),
                               "stroke-width": 1, opacity: 0 }, f.svg);
    const hit = mk("rect", { x: f.pad.l, y: f.pad.t, width: f.iw, height: f.ih, fill: "transparent" }, f.svg);
    hit.addEventListener("mousemove", (e) => {
      const bb = f.svg.getBoundingClientRect();
      const px = (e.clientX - bb.left) / bb.width * f.w;
      let k = Math.round((px - f.pad.l) / f.iw * (nh + nf - 1));
      k = Math.max(0, Math.min(nh + nf - 1, k));
      cross.setAttribute("x1", X(k)); cross.setAttribute("x2", X(k)); cross.setAttribute("opacity", 1);
      let html;
      if (k < nh) {
        html = `<b>${shortTs(w.hist_ts[k])}</b><br>quá khứ · close ${fmt(w.hist_close[k], 2)}`;
      } else {
        const j = k - nh, err = w.pred_close[j] - w.fut_close[j];
        html = `<b>${shortTs(w.fut_ts[j])}</b> · nến +${j + 1}<br>
                thật ${fmt(w.fut_close[j], 2)}<br>dự báo ${fmt(w.pred_close[j], 2)}<br>
                lệch ${signed(err, 2)}`;
      }
      showTip(e, html);
    });
    hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); hideTip(); });

    // facts strip
    const predMove = w.pred_close[nf - 1] - w.anchor;
    const realMove = w.fut_close[nf - 1] - w.anchor;
    const hitDir = Math.sign(predMove) === Math.sign(realMove) && Math.sign(realMove) !== 0;
    const dirTxt = (v) => v > 0 ? "LÊN ▲" : (v < 0 ? "XUỐNG ▼" : "ĐỨNG");
    document.getElementById("windowFacts").innerHTML = `
      <div><span>Thời điểm dự báo</span><b>${shortTs(w.anchor_ts)}</b></div>
      <div><span>Giá lúc đó</span><b>${fmt(w.anchor, 2)}</b></div>
      <div><span>Kronos đoán</span><b>${dirTxt(predMove)} ${signed(predMove, 2)}</b></div>
      <div><span>Thực tế</span><b>${dirTxt(realMove)} ${signed(realMove, 2)}</b></div>
      <div><span>Hướng</span><span class="badge ${hitDir ? "hit" : "miss"}">${hitDir ? "✓ đúng" : "✗ sai"}</span></div>
      <div><span>Sai số cuối kỳ</span><b>${signed(w.pred_close[nf - 1] - w.fut_close[nf - 1], 2)}</b></div>`;

    document.getElementById("pos").textContent = `cửa sổ ${i + 1}/${W.length}`;
    document.getElementById("slider").value = i;
    document.getElementById("prev").disabled = i === 0;
    document.getElementById("next").disabled = i === W.length - 1;
    for (const tr of document.querySelectorAll("#windowTable tbody tr"))
      tr.style.background = (+tr.dataset.i === i) ? css("--wash") : "";
  }
  function goto(i) { cur = Math.max(0, Math.min(W.length - 1, i)); drawWindow(cur); }

  // ---- 2. per-horizon charts ---------------------------------------------
  function barChart(hostId, rows, opts) {
    const host = document.getElementById(hostId);
    const f = frame(host, opts.height || 240, { l: 58, r: opts.padRight || 14, t: 20, b: 34 });
    const vals = rows.map(r => r.v);
    const lo = 0;  // bars are always read from zero
    let hi = opts.max !== undefined ? opts.max : Math.max(...vals, opts.ref ?? -Infinity) * 1.12;
    const Y = yAxis(f, lo, hi, opts.yLabel, opts.dec);
    const bw = f.iw / rows.length;
    const gap = Math.min(6, Math.max(2, bw * 0.22));  // >= 2px surface gap
    const w0 = Math.max(2, bw - gap);
    const y0 = Y(lo);
    rows.forEach((r, k) => {
      const x = f.pad.l + k * bw + gap / 2;
      const y = Y(r.v), h = Math.max(1, y0 - y), rad = Math.min(4, w0 / 2, h);
      const d = `M${x} ${y0} V${y + rad} Q${x} ${y} ${x + rad} ${y} H${x + w0 - rad}`
              + ` Q${x + w0} ${y} ${x + w0} ${y + rad} V${y0} Z`;
      const bar = mk("path", { d, fill: css("--series-1") }, f.svg);
      bar.addEventListener("mousemove", (e) => showTip(e, r.tip));
      bar.addEventListener("mouseleave", hideTip);
      const every = Math.max(1, Math.round(rows.length / 12));
      const isLast = k === rows.length - 1;
      // label every Nth bar plus the last one, unless that would crowd its neighbour
      if (k % every === 0 ? !(isLast && false) : (isLast && (k % every) > every / 2))
        xLabels(f, [{ x: x + w0 / 2, text: r.x }]);
    });
    baseline(f, y0);
    if (opts.ref !== undefined) {
      const y = Y(opts.ref);
      mk("line", { x1: f.pad.l, x2: f.pad.l + f.iw, y1: y, y2: y, stroke: css("--critical"),
                   "stroke-width": 2, "stroke-dasharray": "5 4" }, f.svg);
      (opts.refLabel || []).forEach((line, li) => {
        const tx = mk("text", { x: f.pad.l + f.iw + 8, y: y + 4 + (li - ((opts.refLabel.length - 1) / 2)) * 14,
                                fill: css("--critical"), "font-size": 11,
                                "font-weight": li === 0 ? 700 : 500 }, f.svg);
        tx.textContent = line;
      });
    }
    const tx = mk("text", { x: f.pad.l + f.iw / 2, y: f.h - 2, "text-anchor": "middle",
                            fill: css("--text-muted"), "font-size": 11 }, f.svg);
    tx.textContent = opts.xLabel || "";
  }

  function drawHorizon() {
    const ph = S.per_horizon || [];
    barChart("dirChart", ph.map(r => ({
      x: r.horizon, v: r.dir_acc * 100,
      tip: `<b>+${r.horizon} nến</b><br>đoán đúng hướng ${pct(r.dir_acc, 1)}<br>MAE ${fmt(r.mae, 2)} · RMSE ${fmt(r.rmse, 2)}`,
    })), { ref: 50, refLabel: ["50%", "tung đồng xu"], max: 100, padRight: 92,
           yLabel: "đúng hướng (%)", xLabel: "số nến phía trước", dec: 0, height: 250 });

    barChart("maeChart", ph.map(r => ({
      x: r.horizon, v: r.mae,
      tip: `<b>+${r.horizon} nến</b><br>MAE ${fmt(r.mae, 2)}<br>MAPE ${fmt(r.mape_pct, 3)}%`,
    })), { yLabel: "MAE (đơn vị giá)", xLabel: "số nến phía trước", dec: 1, height: 200 });

    const t = document.getElementById("horizonTable");
    t.innerHTML = "<thead><tr><th>Nến phía trước</th><th>Đúng hướng</th><th>MAE</th><th>RMSE</th><th>MAPE %</th></tr></thead><tbody>"
      + ph.map(r => `<tr><td>+${r.horizon}</td><td><b>${pct(r.dir_acc, 1)}</b></td><td>${fmt(r.mae, 2)}</td>`
                   + `<td>${fmt(r.rmse, 2)}</td><td>${fmt(r.mape_pct, 3)}</td></tr>`).join("")
      + "</tbody>";
  }

  // ---- 3. equity curve ----------------------------------------------------
  function drawEquity() {
    const eq = (S.naive_strategy || {}).equity_curve_pct || [];
    const host = document.getElementById("equityChart");
    if (!eq.length) { host.innerHTML = '<p class="caveat">Không có lệnh nào để vẽ.</p>'; return; }
    const f = frame(host, 240, { l: 58, r: 66, t: 20, b: 34 });
    let lo = Math.min(0, ...eq), hi = Math.max(0, ...eq);
    const padv = (hi - lo || 1) * 0.12; lo -= padv; hi += padv;
    const X = (k) => f.pad.l + (eq.length <= 1 ? 0 : k / (eq.length - 1) * f.iw);
    const Y = yAxis(f, lo, hi, "lãi cộng dồn (%)", 1);
    const y0 = Y(0);
    mk("line", { x1: f.pad.l, x2: f.pad.l + f.iw, y1: y0, y2: y0, stroke: css("--axis"), "stroke-width": 1 }, f.svg);
    const d = eq.map((v, k) => (k ? "L" : "M") + X(k).toFixed(1) + " " + Y(v).toFixed(1)).join(" ");
    mk("path", { d, fill: "none", stroke: css("--series-1"), "stroke-width": 2,
                 "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
    const last = eq[eq.length - 1];
    mk("circle", { cx: X(eq.length - 1), cy: Y(last), r: 4.5, fill: css("--series-1"),
                   stroke: css("--surface"), "stroke-width": 2 }, f.svg);
    const tx = mk("text", { x: X(eq.length - 1) + 9, y: Y(last) + 4,
                            fill: css("--series-1"), "font-size": 11.5, "font-weight": 600,
                            "font-variant-numeric": "tabular-nums" }, f.svg);
    tx.textContent = signed(last, 2) + "%";
    const stepN = Math.max(1, Math.round((eq.length - 1) / 5));
    const marks = [];
    for (let k = 0; k < eq.length; k += stepN) marks.push({ x: X(k), text: String(k + 1) });
    if (marks[marks.length - 1].text !== String(eq.length))
      marks.push({ x: X(eq.length - 1), text: String(eq.length) });
    xLabels(f, marks);
    const xl = mk("text", { x: f.pad.l + f.iw / 2, y: f.h - 2, "text-anchor": "middle",
                            fill: css("--text-muted"), "font-size": 11 }, f.svg);
    xl.textContent = "lệnh thứ";

    const cross = mk("line", { y1: f.pad.t, y2: f.pad.t + f.ih, stroke: css("--axis"),
                               "stroke-width": 1, opacity: 0 }, f.svg);
    const hit = mk("rect", { x: f.pad.l, y: f.pad.t, width: f.iw, height: f.ih, fill: "transparent" }, f.svg);
    hit.addEventListener("mousemove", (e) => {
      const bb = f.svg.getBoundingClientRect();
      let k = Math.round(((e.clientX - bb.left) / bb.width * f.w - f.pad.l) / f.iw * (eq.length - 1));
      k = Math.max(0, Math.min(eq.length - 1, k));
      cross.setAttribute("x1", X(k)); cross.setAttribute("x2", X(k)); cross.setAttribute("opacity", 1);
      const step = k ? eq[k] - eq[k - 1] : eq[0];
      showTip(e, `<b>lệnh ${k + 1}</b><br>lệnh này ${signed(step, 3)}%<br>cộng dồn ${signed(eq[k], 2)}%`);
    });
    hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); hideTip(); });
  }

  // ---- 4. window table ----------------------------------------------------
  function drawTable() {
    const t = document.getElementById("windowTable");
    const rows = W.map((w, i) => {
      const nf = w.fut_close.length;
      const pm = w.pred_close[nf - 1] - w.anchor, rm = w.fut_close[nf - 1] - w.anchor;
      const ok = Math.sign(pm) === Math.sign(rm) && Math.sign(rm) !== 0;
      const ret = (Math.sign(pm) * rm / w.anchor) * 100;
      return `<tr data-i="${i}">
        <td>${i + 1}. ${shortTs(w.anchor_ts)}</td>
        <td>${fmt(w.anchor, 2)}</td>
        <td class="${pm >= 0 ? "up" : "down"}"><b>${signed(pm, 2)}</b></td>
        <td class="${rm >= 0 ? "up" : "down"}"><b>${signed(rm, 2)}</b></td>
        <td><span class="badge ${ok ? "hit" : "miss"}">${ok ? "✓" : "✗"}</span></td>
        <td class="${ret >= 0 ? "up" : "down"}"><b>${signed(ret, 3)}%</b></td>
      </tr>`;
    }).join("");
    t.innerHTML = "<thead><tr><th>Cửa sổ / thời điểm</th><th>Giá gốc</th><th>Kronos đoán</th>"
      + "<th>Thực tế</th><th>Hướng</th><th>Lãi lệnh</th></tr></thead><tbody>" + rows + "</tbody>";
    t.querySelectorAll("tbody tr").forEach(tr => tr.addEventListener("click", () => {
      goto(+tr.dataset.i);
      document.getElementById("windowChart").scrollIntoView({ behavior: "smooth", block: "center" });
    }));
  }

  // ---- extremes -----------------------------------------------------------
  function rankWindows() {
    const err = W.map((w, i) => {
      const nf = w.fut_close.length;
      let s = 0;
      for (let k = 0; k < nf; k++) s += Math.abs(w.pred_close[k] - w.fut_close[k]);
      return { i, mae: s / nf };
    });
    err.sort((a, b) => a.mae - b.mae);
    return { best: err[0].i, worst: err[err.length - 1].i };
  }

  // ---- boot ---------------------------------------------------------------
  const sl = document.getElementById("slider");
  sl.max = W.length - 1;
  sl.addEventListener("input", () => goto(+sl.value));
  document.getElementById("prev").addEventListener("click", () => goto(cur - 1));
  document.getElementById("next").addEventListener("click", () => goto(cur + 1));
  const rk = rankWindows();
  document.getElementById("best").addEventListener("click", () => goto(rk.best));
  document.getElementById("worst").addEventListener("click", () => goto(rk.worst));
  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") goto(cur - 1);
    if (e.key === "ArrowRight") goto(cur + 1);
  });

  function renderAll() { drawWindow(cur); drawHorizon(); drawEquity(); }
  tiles(); drawTable(); renderAll();
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(renderAll, 120); });
})();
</script>
</body>
</html>
"""


def build_html(detail: dict) -> str:
    """Render the detail payload as one standalone HTML page."""
    s = detail["summary"]
    cfg = s.get("config", {})
    verdict = _verdict(detail)
    title = (
        f"Kronos backtest - {detail['symbol']} {detail['timeframe']}"
    )
    meta = " &nbsp;·&nbsp; ".join(
        part for part in [
            f"model <code>{cfg.get('model', '?')}</code>",
            f"dữ liệu <code>{cfg.get('data_start', '?')}</code> → <code>{cfg.get('data_end', '?')}</code>",
            f"nguồn <code>{cfg.get('source', '?')}</code>",
            f"sample_count {cfg.get('sample_count', '?')}, T {cfg.get('T', '?')}, "
            f"top_p {cfg.get('top_p', '?')}"
            + (f", seed {cfg['seed']}" if cfg.get("seed") is not None else ""),
        ] if part
    )

    repl = {
        "__TITLE__": title,
        "__META__": meta,
        "__PRED_LEN__": str(cfg.get("pred_len", s.get("horizon", "?"))),
        "__LOOKBACK__": str(cfg.get("lookback", "?")),
        "__N_WINDOWS__": str(s.get("n_windows", len(detail.get("windows", [])))),
        "__VERDICT_LEVEL__": verdict["level"],
        "__VERDICT_HEADLINE__": verdict["headline"],
        "__VERDICT_DETAIL__": verdict["detail"],
        # Guard against an early </script> inside the embedded JSON.
        "__DATA__": json.dumps(detail, default=_jsonable, ensure_ascii=False).replace("</", "<\\/"),
    }
    html = _TEMPLATE
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


def _jsonable(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:
        pass
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return str(o)


def write_report(detail: dict, path: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_html(detail))
    return path


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Rebuild the interactive HTML report from a saved *_detail.json."
    )
    p.add_argument("detail_json", help="path to a *_detail.json written by backtest.py")
    p.add_argument("-o", "--out", default=None, help="output .html (default: alongside the input)")
    args = p.parse_args(argv)

    with open(args.detail_json, encoding="utf-8") as fh:
        detail = json.load(fh)
    out = args.out or args.detail_json.replace("_detail.json", "_report.html")
    if out == args.detail_json:
        out = os.path.splitext(args.detail_json)[0] + ".html"
    write_report(detail, out)
    print(f"[out] html : {out}")


if __name__ == "__main__":
    main()
