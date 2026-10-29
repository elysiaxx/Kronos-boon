"""
Turn a forward-test session into a readable P&L report.

:mod:`mt5_backtest.live_demo` leaves three files behind per run - a session
JSON, a per-bar decision log, and a per-fill log. This module reads them and
writes one standalone HTML page: the equity curve over real time, every round
trip as its own bar and table row, drawdown, and a verdict that says plainly
whether the profit is distinguishable from luck.

    python -m mt5_backtest.live_report results/XAUUSD_M15_paper_file_..._session.json

The run writes it automatically when it finishes, but a session killed partway
(laptop slept, terminal closed) is still reportable - every log is flushed as
it is written, so point this at the session JSON whenever you want to look.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
from datetime import datetime

from mt5_backtest.report_assets import page


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #

def _read_csv(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _f(row: dict, key: str, default=None):
    """Float from a CSV cell, tolerating blanks written for 'no value'."""
    v = (row.get(key) or "").strip()
    if v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def load_session(session_json: str) -> dict:
    """Read a session JSON plus its sibling decision/fill logs."""
    with open(session_json, encoding="utf-8") as fh:
        session = json.load(fh)
    base = os.path.dirname(os.path.abspath(session_json))
    decisions_rows = _read_csv(os.path.join(base, session.get("trades_csv", "")))
    fills_rows = _read_csv(os.path.join(base, session.get("fills_csv", "")))

    decisions = [{
        "time": r.get("bar_time", ""),
        "anchor": _f(r, "anchor"),
        "target": _f(r, "target"),
        "exp_move_pct": _f(r, "exp_move_pct"),
        "direction": int(_f(r, "direction", 0) or 0),
        "action": r.get("action", ""),
        "fill_price": _f(r, "fill_price"),
        "equity": _f(r, "equity"),
    } for r in decisions_rows]

    trades = [{
        "entry_time": r.get("entry_time", ""),
        "exit_time": r.get("time", ""),
        "side": int(_f(r, "side", 0) or 0),
        "volume": _f(r, "volume", 0.0),
        "entry_price": _f(r, "entry_price"),
        "exit_price": _f(r, "price"),
        "pnl": _f(r, "realized_pnl", 0.0),
    } for r in fills_rows if r.get("action") == "close"]

    return {"session": session, "decisions": decisions, "trades": trades}


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #

def max_drawdown(equity: list) -> tuple:
    """Largest peak-to-trough fall. Returns (absolute, fraction_of_peak, index)."""
    peak, worst, worst_pct, at = None, 0.0, 0.0, 0
    for i, v in enumerate(equity):
        if v is None:
            continue
        peak = v if peak is None else max(peak, v)
        dd = peak - v
        if dd > worst:
            worst, at = dd, i
            worst_pct = dd / peak if peak else 0.0
    return worst, worst_pct, at


def bootstrap_mean(values: list, n_boot: int = 10000, seed: int = 12345) -> dict:
    """Bootstrap the mean: 95% interval and a two-sided p-value against zero.

    Trade P&L is skewed and heavy-tailed, so resampling beats assuming a normal
    distribution - and it needs no scipy.
    """
    n = len(values)
    if n < 2:
        return {"mean": values[0] if n else float("nan"), "ci_low": float("nan"),
                "ci_high": float("nan"), "p_value": float("nan"), "n": n}
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += values[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[min(n_boot - 1, int(0.975 * n_boot))]
    below = sum(1 for m in means if m <= 0) / n_boot
    above = sum(1 for m in means if m >= 0) / n_boot
    return {"mean": sum(values) / n, "ci_low": lo, "ci_high": hi,
            "p_value": min(1.0, 2 * min(below, above)), "n": n}


def compute_stats(data: dict) -> dict:
    session, decisions, trades = data["session"], data["decisions"], data["trades"]
    initial = float(session.get("initial_cash") or 0.0)
    equity = [d["equity"] for d in decisions if d["equity"] is not None]
    final = float(session.get("final_equity") or (equity[-1] if equity else initial))

    pnls = [t["pnl"] for t in trades if t["pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)

    dd, dd_pct, dd_at = max_drawdown(equity)
    boot = bootstrap_mean(pnls) if len(pnls) >= 2 else {
        "mean": pnls[0] if pnls else float("nan"), "ci_low": float("nan"),
        "ci_high": float("nan"), "p_value": float("nan"), "n": len(pnls)}

    # Buy & hold over the same stretch, same size, for a benchmark.
    anchors = [d["anchor"] for d in decisions if d["anchor"] is not None]
    bh_pnl = float("nan")
    if len(anchors) >= 2:
        move = anchors[-1] - anchors[0]
        bh_pnl = move * float(session.get("volume") or 0) * float(session.get("contract_size") or 1)

    exposure = (sum(1 for d in decisions if d["direction"] != 0) / len(decisions)) if decisions else 0.0
    span = _span_hours(decisions)

    return {
        "initial_cash": initial,
        "final_equity": final,
        "net_pnl": final - initial,
        "return_pct": ((final - initial) / initial * 100.0) if initial else float("nan"),
        "n_decisions": len(decisions),
        "n_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "hit_rate": (len(wins) / len(pnls)) if pnls else float("nan"),
        "avg_win": (sum(wins) / len(wins)) if wins else float("nan"),
        "avg_loss": (sum(losses) / len(losses)) if losses else float("nan"),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("nan"),
        "best_trade": max(pnls) if pnls else float("nan"),
        "worst_trade": min(pnls) if pnls else float("nan"),
        "max_drawdown": dd,
        "max_drawdown_pct": dd_pct * 100.0,
        "max_drawdown_at": dd_at,
        "per_trade": boot,
        "buy_hold_pnl": bh_pnl,
        "exposure_pct": exposure * 100.0,
        "span_hours": span,
        "spread_paid": float(session.get("spread_paid") or 0.0),
    }


def _span_hours(decisions: list) -> float:
    stamps = []
    for d in decisions:
        t = str(d.get("time") or "").strip()
        for fmtstr in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                stamps.append(datetime.strptime(t[:19], fmtstr))
                break
            except ValueError:
                continue
    if len(stamps) < 2:
        return float("nan")
    return (max(stamps) - min(stamps)).total_seconds() / 3600.0


# --------------------------------------------------------------------------- #
# verdict
# --------------------------------------------------------------------------- #

def _vn(value: float, dec: int = 2) -> str:
    """Format like the page does: '.' for thousands, ',' for decimals."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "–"
    txt = f"{value:,.{dec}f}"
    return txt.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _signed_vn(value: float, dec: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "–"
    return ("+" if value >= 0 else "") + _vn(value, dec)


def _verdict(stats: dict, session: dict) -> dict:
    n = stats["n_trades"]
    net = stats["net_pnl"]
    p = stats["per_trade"].get("p_value")
    cur = session.get("currency", "")

    if n == 0:
        return {"level": "unknown",
                "headline": "Chưa có lệnh nào đóng - chưa đo được lãi/lỗ.",
                "detail": "Phiên cần chạy đủ lâu để mô hình mở rồi đóng ít nhất một lệnh. "
                          "Hạ --threshold_pct để vào lệnh thường xuyên hơn, hoặc chạy dài hơn."}

    money = f"{_signed_vn(net)} {cur}".strip()
    # worded cases already carry the sign in the word, so show the magnitude there
    magnitude = f"{_vn(abs(net))} {cur}".strip()
    if n < 20:
        return {"level": "warning",
                "headline": f"Lãi/lỗ {money} sau {n} lệnh - cỡ mẫu quá nhỏ để kết luận.",
                "detail": f"Dưới 20 lệnh thì con số nào cũng gần như vô nghĩa: một lệnh may mắn "
                          f"đủ để đảo ngược kết quả. Hãy chạy tới ít nhất 30-50 lệnh rồi xem lại. "
                          f"Hiện tại: {stats['wins']} thắng / {stats['losses']} thua."}

    if p is not None and not math.isnan(p) and p < 0.05 and net > 0:
        level, head = "good", f"Lãi {magnitude} sau {n} lệnh - khác 0 một cách có ý nghĩa thống kê."
    elif p is not None and not math.isnan(p) and p < 0.05 and net < 0:
        level, head = "critical", f"Lỗ {magnitude} sau {n} lệnh - khác 0 một cách có ý nghĩa thống kê."
    else:
        level = "warning"
        head = (f"{'Lãi' if net >= 0 else 'Lỗ'} {magnitude} sau {n} lệnh - "
                f"CHƯA phân biệt được với số 0 (may rủi).")

    b = stats["per_trade"]
    detail = (f"Trung bình {_signed_vn(b['mean'])} mỗi lệnh, khoảng tin cậy 95% là "
              f"{_signed_vn(b['ci_low'])} … {_signed_vn(b['ci_high'])}. p = {_vn(p, 2)}"
              + (" (< 0.05)." if p < 0.05 else " (> 0.05: kết quả này có thể chỉ do may rủi).")
              + f" Thắng {stats['wins']}/{n} lệnh, sụt vốn sâu nhất {_vn(stats['max_drawdown'])}"
              + (f" ({_vn(stats['max_drawdown_pct'], 1)}%)." if stats["max_drawdown_pct"] else "."))
    if b["ci_low"] < 0 < b["ci_high"]:
        detail += " Khoảng tin cậy trùm qua 0, nên chưa thể nói là có lợi thế."
    return {"level": level, "headline": head, "detail": detail}


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #

_BODY = r"""
<header>
  <h1>__TITLE__</h1>
  <p class="sub">Forward-test: mỗi khi một nến <b>__TF__</b> đóng, Kronos dự báo __PRED_LEN__ nến tới; nếu mức biến động dự báo ở mốc +__EVAL_H__ vượt <b>__THRESHOLD__%</b> thì vào lệnh theo hướng đó. __BROKER_NOTE__</p>
  <p class="meta">__META__</p>
</header>

<div class="card verdict __VERDICT_LEVEL__">
  <div class="dot"></div>
  <div>
    <p class="headline">__VERDICT_HEADLINE__</p>
    <p class="body">__VERDICT_DETAIL__</p>
  </div>
</div>
__SPREAD_WARN__

<div class="tiles" id="tiles"></div>

<h2><span class="num">1.</span>Vốn thay đổi thế nào theo thời gian</h2>
<p class="sub">Mỗi điểm là một nến đóng. Đường đi lên là đang lãi, đi xuống là đang lỗ &mdash; kể cả phần lãi/lỗ của lệnh chưa đóng.</p>
<div class="card">
  <div class="chart" id="equityChart"></div>
  <p class="caveat" id="ddNote"></p>
</div>

<h2><span class="num">2.</span>Từng lệnh lãi hay lỗ</h2>
<p class="sub">Mỗi cột là một lệnh đã đóng. Cột xanh lá là lệnh lãi, cột đỏ là lệnh lỗ.</p>
<div class="card">
  <div class="legend">
    <span class="k"><span class="sw" style="background:var(--good)"></span>Lệnh lãi</span>
    <span class="k"><span class="sw" style="background:var(--critical)"></span>Lệnh lỗ</span>
  </div>
  <div class="chart" id="tradeChart"></div>
  <p class="caveat">Một chuỗi lệnh lỗ liên tiếp là bình thường ngay cả với chiến lược tốt. Điều cần nhìn là tổng thể và mức sụt vốn, không phải một cột lẻ.</p>
</div>

<h2><span class="num">3.</span>Bảng từng lệnh</h2>
<div class="card" style="padding:0; overflow:hidden">
  <div class="scroll" style="border:0"><table id="tradeTable"></table></div>
</div>

<h2><span class="num">4.</span>Nhật ký quyết định theo từng nến</h2>
<p class="sub">Mô hình nghĩ gì ở mỗi nến, kể cả những nến nó chọn đứng ngoài.</p>
<details>
  <summary>Mở nhật ký (__N_DECISIONS__ dòng)</summary>
  <div class="scroll" style="margin-top:10px"><table id="decisionTable"></table></div>
</details>

<h2><span class="num">5.</span>Đọc kết quả này thế nào</h2>
<div class="card">
  <dl class="gloss">
    <dt>Net P&amp;L / Return</dt>
    <dd>Tổng lãi lỗ bằng tiền và theo % vốn khởi điểm. Đây là con số cuối cùng, đã gồm cả spread nếu bạn khai báo.</dd>
    <dt>Hit rate &amp; Profit factor</dt>
    <dd>Hit rate là tỷ lệ lệnh lãi. Profit factor = tổng lãi chia tổng lỗ: trên 1 là có lãi ròng, dưới 1 là lỗ. Hit rate cao vẫn có thể lỗ nếu vài lệnh thua rất to.</dd>
    <dt>Max drawdown &mdash; sụt vốn sâu nhất</dt>
    <dd>Khoảng tụt lớn nhất từ đỉnh vốn xuống đáy sau đó. Đây là mức đau nhất bạn phải chịu đựng trong phiên, và thường quan trọng hơn con số lãi cuối.</dd>
    <dt>Khoảng tin cậy &amp; p-value mỗi lệnh</dt>
    <dd>Tính bằng bootstrap: lấy mẫu lại chính các lệnh đã có hàng nghìn lần để xem lãi trung bình dao động tới đâu. Nếu khoảng tin cậy còn trùm qua 0, hoặc p &gt; 0.05, thì chưa phân biệt được kết quả với may rủi, dù tổng đang dương.</dd>
    <dt>So với mua &amp; giữ</dt>
    <dd>Lãi lỗ nếu chỉ mua một lần ở đầu phiên với cùng khối lượng và giữ tới cuối. Nếu chiến lược không hơn được con số này thì mọi phức tạp đều vô ích.</dd>
    <dt>Exposure &mdash; thời gian ở trong thị trường</dt>
    <dd>Tỷ lệ số nến có vị thế mở. Exposure thấp mà vẫn lãi là dấu hiệu tốt; exposure gần 100% nghĩa là gần như luôn ôm rủi ro.</dd>
  </dl>
  <p class="caveat">__PAPER_NOTE__ Kết quả một phiên không dự đoán được phiên sau. Đây không phải khuyến nghị đầu tư.</p>
</div>
"""

_PAGE_JS = r"""
(function () {
  "use strict";
  const { fmt, signed, pct, shortTs, css, barChart, lineChart, onResize } = Viz;
  const D = JSON.parse(document.getElementById("payload").textContent);
  const S = D.stats, SES = D.session, TR = D.trades, DEC = D.decisions;
  const CUR = SES.currency || "";
  const money = (v, d) => signed(v, d === undefined ? 2 : d) + (CUR ? " " + CUR : "");

  function tiles() {
    const b = S.per_trade || {};
    const list = [
      { label: "Lãi/lỗ ròng", en: "net P&L", value: money(S.net_pnl),
        cls: S.net_pnl >= 0 ? "up" : "down",
        note: `Vốn ${fmt(S.initial_cash, 0)} → ${fmt(S.final_equity, 2)} (${signed(S.return_pct, 2)}%).` },
      { label: "Số lệnh & tỷ lệ thắng", en: "trades / hit rate",
        value: `${S.n_trades} · ${pct(S.hit_rate, 0)}`,
        note: `${S.wins} thắng, ${S.losses} thua. Trung bình thắng ${fmt(S.avg_win, 2)}, thua ${fmt(S.avg_loss, 2)}.` },
      { label: "Sụt vốn sâu nhất", en: "max drawdown",
        value: fmt(S.max_drawdown, 2), cls: "down",
        note: `${fmt(S.max_drawdown_pct, 1)}% so với đỉnh vốn. Đây là mức đau nhất trong phiên.` },
      { label: "Profit factor", en: "tổng lãi / tổng lỗ", value: fmt(S.profit_factor, 2),
        note: "Trên 1 là lãi ròng, dưới 1 là lỗ. Lệnh tốt nhất "
              + fmt(S.best_trade, 2) + ", tệ nhất " + fmt(S.worst_trade, 2) + "." },
      { label: "Trung bình mỗi lệnh", en: "per trade (bootstrap)",
        value: money(b.mean), cls: b.mean >= 0 ? "up" : "down",
        note: `Khoảng tin cậy 95%: ${signed(b.ci_low, 2)} … ${signed(b.ci_high, 2)}. p = ${fmt(b.p_value, 2)}.` },
      { label: "So với mua & giữ", en: "vs buy & hold", value: money(S.buy_hold_pnl),
        note: `Nếu chỉ mua ở đầu phiên và giữ tới cuối, cùng ${fmt(SES.volume, 2)} lot. `
              + (S.net_pnl > S.buy_hold_pnl ? "Chiến lược đang hơn." : "Chiến lược đang không hơn.") },
      { label: "Thời gian trong thị trường", en: "exposure", value: fmt(S.exposure_pct, 0) + "%",
        note: `${S.n_decisions} nến đã xử lý`
              + (S.span_hours ? `, trải ${fmt(S.span_hours, 1)} giờ.` : ".") },
      (SES.broker === "paper"
        ? { label: "Spread đã trả", en: "spread cost", value: fmt(S.spread_paid, 2),
            note: SES.spread > 0 ? `${fmt(SES.spread, 2)} đơn vị giá mỗi vòng, đã trừ khỏi lãi.`
                                 : "Chưa khai báo spread — lãi đang bị thổi phồng." }
        : { label: "Chi phí giao dịch", en: "costs", value: "theo sàn",
            note: "Spread, slippage, commission và phí qua đêm đã nằm trong lãi lỗ "
                  + "từng lệnh do sàn báo về." }),
    ];
    document.getElementById("tiles").innerHTML = list.map(t => `
      <div class="tile">
        <div class="label">${t.label} <span class="en">(${t.en})</span></div>
        <div class="value ${t.cls || ""}">${t.value}</div>
        <div class="note">${t.note}</div>
      </div>`).join("");
  }

  function drawEquity() {
    const eq = DEC.filter(d => d.equity !== null);
    if (eq.length < 2) {
      document.getElementById("equityChart").innerHTML =
        '<p class="caveat">Chưa đủ dữ liệu để vẽ đường vốn.</p>';
      return;
    }
    const every = Math.max(1, Math.round((eq.length - 1) / 5));
    const xTicks = [];
    for (let k = 0; k < eq.length; k += every) xTicks.push({ v: k, text: shortTs(eq[k].time) });
    lineChart("equityChart", {
      height: 260, padRight: 86, yLabel: `vốn (${CUR || "tài khoản"})`, yDec: 0,
      hline: S.initial_cash,
      series: [{ pts: eq.map((d, k) => [k, d.equity]), color: css("--series-1"),
                 endLabel: fmt(eq[eq.length - 1].equity, 2) }],
      xTicks,
      snap: eq.map((_, k) => k),
      tooltip: (k) => {
        const d = eq[k];
        return `<b>${shortTs(d.time)}</b><br>vốn ${fmt(d.equity, 2)} `
             + `(${signed(d.equity - S.initial_cash, 2)})<br>giá ${fmt(d.anchor, 2)} · ${d.action}`;
      },
    });
    document.getElementById("ddNote").textContent =
      `Đường ngang là vốn khởi điểm ${fmt(S.initial_cash, 0)}. Sụt vốn sâu nhất `
      + `${fmt(S.max_drawdown, 2)} (${fmt(S.max_drawdown_pct, 1)}%).`;
  }

  function drawTrades() {
    if (!TR.length) {
      document.getElementById("tradeChart").innerHTML =
        '<p class="caveat">Chưa có lệnh nào đóng.</p>';
      return;
    }
    barChart("tradeChart", TR.map((t, i) => ({
      x: i + 1, v: t.pnl,
      tip: `<b>lệnh ${i + 1}</b> · ${t.side > 0 ? "MUA" : "BÁN"}<br>`
         + `vào ${shortTs(t.entry_time)} @ ${fmt(t.entry_price, 2)}<br>`
         + `ra ${shortTs(t.exit_time)} @ ${fmt(t.exit_price, 2)}<br>`
         + `lãi/lỗ <b>${signed(t.pnl, 2)}</b>`,
    })), { height: 230, yLabel: `lãi/lỗ mỗi lệnh (${CUR || "tài khoản"})`, dec: 1,
           xTitle: "lệnh thứ",
           colorFor: (r) => r.v >= 0 ? css("--good") : css("--critical") });
  }

  function drawTradeTable() {
    const t = document.getElementById("tradeTable");
    if (!TR.length) { t.innerHTML = "<tbody><tr><td>Chưa có lệnh nào đóng.</td></tr></tbody>"; return; }
    let run = 0;
    t.innerHTML = "<thead><tr><th>#</th><th>Hướng</th><th>Vào lúc</th><th>Giá vào</th>"
      + "<th>Ra lúc</th><th>Giá ra</th><th>Lãi/lỗ</th><th>Cộng dồn</th></tr></thead><tbody>"
      + TR.map((tr, i) => {
          run += tr.pnl;
          return `<tr>
            <td>${i + 1}</td>
            <td><span class="badge ${tr.side > 0 ? "long" : "short"}">${tr.side > 0 ? "MUA" : "BÁN"}</span></td>
            <td>${shortTs(tr.entry_time)}</td><td>${fmt(tr.entry_price, 2)}</td>
            <td>${shortTs(tr.exit_time)}</td><td>${fmt(tr.exit_price, 2)}</td>
            <td class="${tr.pnl >= 0 ? "up" : "down"}"><b>${signed(tr.pnl, 2)}</b></td>
            <td class="${run >= 0 ? "up" : "down"}"><b>${signed(run, 2)}</b></td>
          </tr>`;
        }).join("")
      + "</tbody>";
  }

  function drawDecisions() {
    const dirTxt = (d) => d > 0 ? "LÊN ▲" : (d < 0 ? "XUỐNG ▼" : "đứng ngoài");
    document.getElementById("decisionTable").innerHTML =
      "<thead><tr><th>Nến</th><th>Giá</th><th>Kronos đoán</th><th>Biến động dự báo</th>"
      + "<th>Hành động</th><th>Vốn</th></tr></thead><tbody>"
      + DEC.map(d => `<tr>
          <td>${shortTs(d.time)}</td>
          <td>${fmt(d.anchor, 2)}</td>
          <td class="${d.direction > 0 ? "up" : (d.direction < 0 ? "down" : "")}"><b>${dirTxt(d.direction)}</b></td>
          <td>${signed(d.exp_move_pct, 3)}%</td>
          <td>${d.action}</td>
          <td>${fmt(d.equity, 2)}</td>
        </tr>`).join("")
      + "</tbody>";
  }

  const renderAll = () => { drawEquity(); drawTrades(); };
  tiles(); drawTradeTable(); drawDecisions(); renderAll();
  onResize(renderAll);
})();
"""


def build_html(data: dict) -> str:
    session, stats = data["session"], data["stats"]
    sym = session.get("symbol", "?")
    tf = session.get("timeframe", "?")
    title = f"Kronos forward-test - {sym} {tf}"

    broker, feed = session.get("broker", "?"), session.get("feed", "?")
    if broker == "paper":
        broker_note = ("Lệnh được <b>mô phỏng</b> (paper), giá là giá thật từ feed "
                       f"<code>{feed}</code> — không có lệnh nào gửi lên sàn.")
        paper_note = ("Đây là lệnh mô phỏng: khớp ngay tại giá đóng nến, không trượt giá, "
                      "không phí qua đêm, không bị từ chối lệnh. Tài khoản demo thật sẽ "
                      "khớp kém hơn một chút.")
    elif broker == "file":
        broker_note = ("Lệnh được <b>đặt thật</b> trên tài khoản bởi EA "
                       "<code>KronosTrader.mq5</code> trong terminal MT5.")
        paper_note = ("Lãi lỗ ở đây lấy thẳng từ deal của sàn, nên đã gồm spread, slippage, "
                      "commission và phí qua đêm thật.")
    else:
        broker_note = f"Lệnh được <b>gửi thật</b> lên tài khoản MT5 qua feed <code>{feed}</code>."
        paper_note = "Số liệu lấy từ tài khoản MT5, đã gồm điều kiện khớp lệnh thật của sàn."

    meta = " &nbsp;·&nbsp; ".join([
        f"model <code>{session.get('model', '?')}</code>",
        f"broker <code>{broker}</code> · feed <code>{feed}</code>",
        f"{session.get('volume', '?')} lot · spread {session.get('spread', 0)}",
        f"bắt đầu <code>{session.get('started_at', '?')}</code>"
        + (f" → <code>{session['ended_at']}</code>" if session.get("ended_at") else " (đang chạy)"),
    ])

    spread_warn = ""
    if broker == "paper" and not session.get("spread"):
        spread_warn = (
            '<div class="card"><p class="caveat warn" style="margin:0">'
            '<b>Chưa tính spread.</b> Phiên này chạy với <code>--spread 0</code>, nghĩa là mọi lệnh '
            'được khớp đúng giá giữa — không sàn nào cho như vậy. Với XAUUSD, spread 0.30 ăn khoảng '
            '0.30 × khối lượng × 100 mỗi vòng lệnh; chạy lại với <code>--spread 0.30</code> để biết '
            'con số thật.</p></div>'
        )

    body = _BODY
    for key, value in (
        ("__TITLE__", title),
        ("__TF__", tf),
        ("__PRED_LEN__", str(session.get("pred_len", "?"))),
        ("__EVAL_H__", str(session.get("eval_horizon", "?"))),
        ("__THRESHOLD__", str(session.get("threshold_pct", "?"))),
        ("__BROKER_NOTE__", broker_note),
        ("__PAPER_NOTE__", paper_note),
        ("__META__", meta),
        ("__SPREAD_WARN__", spread_warn),
        ("__N_DECISIONS__", str(stats["n_decisions"])),
        ("__VERDICT_LEVEL__", data["verdict"]["level"]),
        ("__VERDICT_HEADLINE__", data["verdict"]["headline"]),
        ("__VERDICT_DETAIL__", data["verdict"]["detail"]),
    ):
        body = body.replace(key, value)
    return page(title, body, data, _PAGE_JS)


def build_report(session_json: str) -> dict:
    data = load_session(session_json)
    data["stats"] = compute_stats(data)
    data["verdict"] = _verdict(data["stats"], data["session"])
    return data


def write_live_report(session_json: str, out_path: str | None = None) -> str:
    data = build_report(session_json)
    out_path = out_path or session_json.replace("_session.json", "_report.html")
    if out_path == session_json:
        out_path = os.path.splitext(session_json)[0] + ".html"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(build_html(data))
    return out_path


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Build the forward-test P&L report from a session JSON."
    )
    p.add_argument("session_json", help="path to a *_session.json written by live_demo.py")
    p.add_argument("-o", "--out", default=None, help="output .html")
    args = p.parse_args(argv)
    out = write_live_report(args.session_json, args.out)
    data = build_report(args.session_json)
    s = data["stats"]
    print(f"[out] trades={s['n_trades']}  net={s['net_pnl']:+.2f}  "
          f"return={s['return_pct']:+.2f}%  maxDD={s['max_drawdown']:.2f}")
    print(f"[out] html : {out}")


if __name__ == "__main__":
    main()
