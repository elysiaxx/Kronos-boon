"""
Guard the forward-test report's headline claims.

The verdict card is the single sentence someone reads to decide whether the
model has an edge, so the ways it can be confidently wrong matter more than
the ways it can be imprecise.

Run directly - no pytest needed:

    python -m tests.test_live_report
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_backtest.live_report import build_report, compute_stats, _verdict  # noqa: E402


def _session(tmp, trades, final_equity, initial=10_000.0, spread=0.30):
    """Write a session whose closed trades are exactly ``trades`` (a P&L list)."""
    fills = [["time", "action", "side", "volume", "price",
              "entry_time", "entry_price", "realized_pnl"]]
    decisions = [["bar_time", "anchor", "target", "exp_move_pct",
                  "direction", "action", "fill_price", "equity"]]
    equity = initial
    for i, pnl in enumerate(trades):
        t = f"2026-10-29 {i // 4:02d}:{(i % 4) * 15:02d}:00"
        fills.append([t, "open_long", 1, "0.0100", "4000.00000", "", "", "0.00000"])
        fills.append([t, "close", 1, "0.0100", "4001.00000", t, "4000.00000", f"{pnl:.5f}"])
        equity += pnl
        decisions.append([t, "4000.00000", "4001.00000", "0.1000", 1, "close", "", f"{equity:.2f}"])
    with open(os.path.join(tmp, "f.csv"), "w", newline="") as fh:
        csv.writer(fh).writerows(fills)
    with open(os.path.join(tmp, "t.csv"), "w", newline="") as fh:
        csv.writer(fh).writerows(decisions)
    path = os.path.join(tmp, "s.json")
    json.dump({"symbol": "XAUUSD", "timeframe": "M15", "broker": "paper", "feed": "yahoo",
               "volume": 0.01, "spread": spread, "contract_size": 100, "initial_cash": initial,
               "currency": "USD", "trades_csv": "t.csv", "fills_csv": "f.csv",
               "final_equity": final_equity, "eval_horizon": 8, "threshold_pct": 0.15},
              open(path, "w"))
    return path


def test_open_position_cannot_turn_losses_into_a_significant_profit():
    """20 straight losers plus one unclosed winner must not read as an edge."""
    with tempfile.TemporaryDirectory() as tmp:
        # every closed trade lost 5; one open position is +500 on paper
        path = _session(tmp, [-5.0] * 20, final_equity=10_400.0)
        data = build_report(path)
        st, v = data["stats"], data["verdict"]
        assert st["realized_pnl"] == -100.0, st["realized_pnl"]
        assert st["unrealized_pnl"] == 500.0, st["unrealized_pnl"]
        assert st["per_trade"]["p_value"] < 0.05
        assert v["level"] != "good", f"reported an edge: {v['headline']}"
        assert "ngược dấu" in v["headline"], v["headline"]
        print("  ok: a floating winner cannot label 20 losing trades an edge")


def test_consistent_loss_is_still_called_a_loss():
    with tempfile.TemporaryDirectory() as tmp:
        path = _session(tmp, [-5.0] * 20, final_equity=9_900.0)
        v = build_report(path)["verdict"]
        assert v["level"] == "critical", v
        assert v["headline"].startswith("Lỗ"), v["headline"]
        print("  ok: a consistent loss is reported as a significant loss")


def test_consistent_profit_is_still_called_a_profit():
    with tempfile.TemporaryDirectory() as tmp:
        path = _session(tmp, [5.0] * 20, final_equity=10_100.0)
        v = build_report(path)["verdict"]
        assert v["level"] == "good", v
        assert v["headline"].startswith("Lãi"), v["headline"]
        print("  ok: a consistent profit is still reported as an edge")


def test_unknown_pnl_trades_are_excluded_not_poisonous():
    with tempfile.TemporaryDirectory() as tmp:
        path = _session(tmp, [5.0, -3.0, 4.0], final_equity=10_006.0)
        # blank one close's realized_pnl into NaN, as an unreadable deal would
        rows = list(csv.reader(open(os.path.join(tmp, "f.csv"))))
        for r in rows[1:]:
            if r[1] == "close":
                r[7] = "nan"
                break
        csv.writer(open(os.path.join(tmp, "f.csv"), "w", newline="")).writerows(rows)
        st = build_report(path)["stats"]
        assert st["trades_unknown_pnl"] == 1, st["trades_unknown_pnl"]
        assert st["n_trades"] == 2, st["n_trades"]
        assert math.isfinite(st["profit_factor"]), st["profit_factor"]
        print("  ok: an unreadable trade is excluded and counted, not spread through the stats")


def test_no_trades_yet_is_honest():
    with tempfile.TemporaryDirectory() as tmp:
        path = _session(tmp, [], final_equity=10_000.0)
        v = build_report(path)["verdict"]
        assert v["level"] == "unknown", v
        print("  ok: a session with no closed trade says so")


def test_small_sample_is_never_called_significant():
    with tempfile.TemporaryDirectory() as tmp:
        path = _session(tmp, [5.0] * 5, final_equity=10_025.0)
        v = build_report(path)["verdict"]
        assert v["level"] == "warning" and "cỡ mẫu quá nhỏ" in v["headline"], v
        print("  ok: five winning trades are called too small a sample")


TESTS = [test_open_position_cannot_turn_losses_into_a_significant_profit,
         test_consistent_loss_is_still_called_a_loss,
         test_consistent_profit_is_still_called_a_profit,
         test_unknown_pnl_trades_are_excluded_not_poisonous,
         test_no_trades_yet_is_honest,
         test_small_sample_is_never_called_significant]

if __name__ == "__main__":
    for t in TESTS:
        print(t.__name__)
        t()
    print(f"\n{len(TESTS)} report tests passed")
