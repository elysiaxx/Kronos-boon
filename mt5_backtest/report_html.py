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

Chrome and layout live in :mod:`mt5_backtest.report_assets`, shared with the
forward-test P&L report.
"""

from __future__ import annotations

import argparse
import json
import os

from mt5_backtest.report_assets import page


# --------------------------------------------------------------------------- #
# detail payload
# --------------------------------------------------------------------------- #

def build_detail(summary: dict, windows: list, symbol: str, timeframe: str) -> dict:
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
    if lo < 50.0 < hi:
        detail_txt += " Vì khoảng tin cậy vẫn trùm qua mốc 50%, cần nhiều cửa sổ hơn để kết luận."
    return {"level": level, "headline": headline, "detail": detail_txt}


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #

_BODY = r"""
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
"""

_PAGE_JS = r"""
(function () {
  "use strict";
  const { fmt, pct, signed, shortTs, css, barChart, lineChart, indexTicks, onResize } = Viz;
  const D = JSON.parse(document.getElementById("payload").textContent);
  const S = D.summary, W = D.windows;

  // ---- stat tiles ---------------------------------------------------------
  function tiles() {
    const pe = S.point_errors_overall || {}, st = S.naive_strategy || {}, ds = S.directional_stats || {};
    const list = [
      { label: "Đoán đúng hướng", en: "directional accuracy",
        value: pct(S.directional_accuracy_at_eval_horizon, 0),
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
  let cur = 0;

  function drawWindow(i) {
    const w = W[i];
    const nh = w.hist_close.length, nf = w.fut_close.length;
    const anchorX = nh - 1;
    lineChart("windowChart", {
      height: 320, padRight: 104, yLabel: "giá (close)", yDec: 0,
      xDomain: [0, nh + nf - 1],
      vline: { v: anchorX, label: "thời điểm dự báo" },
      series: [
        { pts: w.hist_close.map((v, k) => [k, v]), color: css("--text-muted"), endDot: false },
        // both future paths start at the anchor so the join is visible
        { pts: [[anchorX, w.anchor]].concat(w.fut_close.map((v, k) => [anchorX + 1 + k, v])),
          color: css("--series-1"), endLabel: "thật " + fmt(w.fut_close[nf - 1], 1) },
        { pts: [[anchorX, w.anchor]].concat(w.pred_close.map((v, k) => [anchorX + 1 + k, v])),
          color: css("--series-2"), dash: "6 4", endLabel: "dự báo " + fmt(w.pred_close[nf - 1], 1) },
      ],
      snap: Array.from({ length: nh + nf }, (_, k) => k),
      tooltip: (k) => {
        if (k < nh) return `<b>${shortTs(w.hist_ts[k])}</b><br>quá khứ · close ${fmt(w.hist_close[k], 2)}`;
        const j = k - nh;
        if (j >= nf) return "";
        return `<b>${shortTs(w.fut_ts[j])}</b> · nến +${j + 1}<br>thật ${fmt(w.fut_close[j], 2)}`
             + `<br>dự báo ${fmt(w.pred_close[j], 2)}<br>lệch ${signed(w.pred_close[j] - w.fut_close[j], 2)}`;
      },
    });

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
  const goto = (i) => { cur = Math.max(0, Math.min(W.length - 1, i)); drawWindow(cur); };

  // ---- 2. per-horizon -----------------------------------------------------
  function drawHorizon() {
    const ph = S.per_horizon || [];
    barChart("dirChart", ph.map(r => ({
      x: r.horizon, v: r.dir_acc * 100,
      tip: `<b>+${r.horizon} nến</b><br>đoán đúng hướng ${pct(r.dir_acc, 1)}<br>MAE ${fmt(r.mae, 2)} · RMSE ${fmt(r.rmse, 2)}`,
    })), { ref: 50, refLabel: ["50%", "tung đồng xu"], max: 100, padRight: 92,
           yLabel: "đúng hướng (%)", xTitle: "số nến phía trước", dec: 0, height: 250 });

    barChart("maeChart", ph.map(r => ({
      x: r.horizon, v: r.mae,
      tip: `<b>+${r.horizon} nến</b><br>MAE ${fmt(r.mae, 2)}<br>MAPE ${fmt(r.mape_pct, 3)}%`,
    })), { yLabel: "MAE (đơn vị giá)", xTitle: "số nến phía trước", dec: 1, height: 200 });

    document.getElementById("horizonTable").innerHTML =
      "<thead><tr><th>Nến phía trước</th><th>Đúng hướng</th><th>MAE</th><th>RMSE</th><th>MAPE %</th></tr></thead><tbody>"
      + ph.map(r => `<tr><td>+${r.horizon}</td><td><b>${pct(r.dir_acc, 1)}</b></td><td>${fmt(r.mae, 2)}</td>`
                  + `<td>${fmt(r.rmse, 2)}</td><td>${fmt(r.mape_pct, 3)}</td></tr>`).join("")
      + "</tbody>";
  }

  // ---- 3. equity ----------------------------------------------------------
  function drawEquity() {
    const eq = (S.naive_strategy || {}).equity_curve_pct || [];
    if (!eq.length) {
      document.getElementById("equityChart").innerHTML = '<p class="caveat">Không có lệnh nào để vẽ.</p>';
      return;
    }
    lineChart("equityChart", {
      height: 240, padRight: 66, yLabel: "lãi cộng dồn (%)", yDec: 1,
      xTitle: "lệnh thứ", hline: 0,
      series: [{ pts: eq.map((v, k) => [k, v]), color: css("--series-1"),
                 endLabel: signed(eq[eq.length - 1], 2) + "%" }],
      xTicks: indexTicks(eq.length, 5),
      snap: eq.map((_, k) => k),
      tooltip: (k) => `<b>lệnh ${k + 1}</b><br>lệnh này ${signed(k ? eq[k] - eq[k - 1] : eq[0], 3)}%`
                    + `<br>cộng dồn ${signed(eq[k], 2)}%`,
    });
  }

  // ---- 4. table -----------------------------------------------------------
  function drawTable() {
    const t = document.getElementById("windowTable");
    t.innerHTML = "<thead><tr><th>Cửa sổ / thời điểm</th><th>Giá gốc</th><th>Kronos đoán</th>"
      + "<th>Thực tế</th><th>Hướng</th><th>Lãi lệnh</th></tr></thead><tbody>"
      + W.map((w, i) => {
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
        }).join("")
      + "</tbody>";
    t.querySelectorAll("tbody tr").forEach(tr => tr.addEventListener("click", () => {
      goto(+tr.dataset.i);
      document.getElementById("windowChart").scrollIntoView({ behavior: "smooth", block: "center" });
    }));
  }

  // ---- extremes, by mean absolute error over the window -------------------
  function rankWindows() {
    const err = W.map((w, i) => {
      let s = 0;
      for (let k = 0; k < w.fut_close.length; k++) s += Math.abs(w.pred_close[k] - w.fut_close[k]);
      return { i, mae: s / w.fut_close.length };
    }).sort((a, b) => a.mae - b.mae);
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

  const renderAll = () => { drawWindow(cur); drawHorizon(); drawEquity(); };
  tiles(); drawTable(); renderAll();
  onResize(renderAll);
})();
"""


def build_html(detail: dict) -> str:
    """Render the detail payload as one standalone HTML page."""
    s = detail["summary"]
    cfg = s.get("config", {})
    verdict = _verdict(detail)
    title = f"Kronos backtest - {detail['symbol']} {detail['timeframe']}"
    meta = " &nbsp;·&nbsp; ".join([
        f"model <code>{cfg.get('model', '?')}</code>",
        f"dữ liệu <code>{cfg.get('data_start', '?')}</code> → <code>{cfg.get('data_end', '?')}</code>",
        f"nguồn <code>{cfg.get('source', '?')}</code>",
        f"sample_count {cfg.get('sample_count', '?')}, T {cfg.get('T', '?')}, "
        f"top_p {cfg.get('top_p', '?')}"
        + (f", seed {cfg['seed']}" if cfg.get("seed") is not None else ""),
    ])

    body = _BODY
    for key, value in (
        ("__TITLE__", title),
        ("__META__", meta),
        ("__PRED_LEN__", str(cfg.get("pred_len", s.get("horizon", "?")))),
        ("__LOOKBACK__", str(cfg.get("lookback", "?"))),
        ("__N_WINDOWS__", str(s.get("n_windows", len(detail.get("windows", []))))),
        ("__VERDICT_LEVEL__", verdict["level"]),
        ("__VERDICT_HEADLINE__", verdict["headline"]),
        ("__VERDICT_DETAIL__", verdict["detail"]),
    ):
        body = body.replace(key, value)
    return page(title, body, detail, _PAGE_JS)


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
