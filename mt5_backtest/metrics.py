"""
Accuracy and (naive) trading metrics for walk-forward Kronos forecasts.

Everything here operates on plain numpy arrays so it is trivially testable
and has no dependency on torch or MT5.

A single "window" is one forecast: given the last observed close (the
*anchor*), the model outputs ``pred_len`` future closes which we compare to
the realized future closes.

Two families of metrics are produced:

1. Point-accuracy of the predicted close path
     - MAE, RMSE, MAPE (%), both overall and per horizon step.
2. Directional usefulness (the part that actually matters for trading)
     - directional accuracy: did ``sign(pred - anchor)`` match
       ``sign(actual - anchor)`` at a chosen horizon, and per step?
     - a naive long/short strategy driven by the predicted direction, so the
       forecast is scored in return space (hit rate, total/avg return, a
       simple Sharpe). This is intentionally crude and is NOT investment
       advice; it only turns "is the forecast directionally useful" into one
       number.
"""

from __future__ import annotations

from typing import List

import numpy as np


def _safe_mape(actual: np.ndarray, pred: np.ndarray) -> float:
    denom = np.abs(actual)
    mask = denom > 1e-12
    if not mask.any():
        return float("nan")
    return float(np.mean(np.abs((actual[mask] - pred[mask]) / denom[mask])) * 100.0)


def point_errors(actual_close: np.ndarray, pred_close: np.ndarray) -> dict:
    """MAE / RMSE / MAPE over a flat set of (actual, predicted) close pairs."""
    actual_close = np.asarray(actual_close, dtype=float)
    pred_close = np.asarray(pred_close, dtype=float)
    err = pred_close - actual_close
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mape_pct": _safe_mape(actual_close, pred_close),
        "n": int(actual_close.size),
    }


def directional_accuracy(anchor: np.ndarray, actual_close: np.ndarray, pred_close: np.ndarray) -> float:
    """Fraction of windows where predicted move direction matched the real one.

    ``anchor``, ``actual_close`` and ``pred_close`` are 1-D arrays of the same
    length, each taken at the SAME horizon across windows. A flat prediction
    (exactly zero move) is counted as a miss unless the market was also flat.
    """
    anchor = np.asarray(anchor, dtype=float)
    a_dir = np.sign(np.asarray(actual_close, dtype=float) - anchor)
    p_dir = np.sign(np.asarray(pred_close, dtype=float) - anchor)
    if a_dir.size == 0:
        return float("nan")
    return float(np.mean(a_dir == p_dir))


def naive_strategy(
    anchor: np.ndarray,
    actual_close: np.ndarray,
    pred_close: np.ndarray,
    cost_pct: float = 0.0,
) -> dict:
    """Score a direction-only strategy at a single horizon.

    For each window: go long if the model predicts the close at the horizon
    above the anchor, short if below, flat if equal. The realized return is
    the actual close move over that horizon (minus a round-trip ``cost_pct``
    in percent, if given). Returns are expressed in percent of anchor price.
    """
    anchor = np.asarray(anchor, dtype=float)
    actual_close = np.asarray(actual_close, dtype=float)
    pred_close = np.asarray(pred_close, dtype=float)

    signal = np.sign(pred_close - anchor)                 # +1 long, -1 short, 0 flat
    market_ret = (actual_close - anchor) / anchor * 100.0 # percent move
    trade_ret = signal * market_ret
    taken = signal != 0
    if taken.any():
        trade_ret = trade_ret.copy()
        trade_ret[taken] -= cost_pct

    traded = trade_ret[taken]
    n_trades = int(taken.sum())
    wins = int(np.sum(traded > 0))
    std = float(np.std(traded)) if n_trades > 1 else 0.0
    return {
        "n_trades": n_trades,
        "hit_rate": float(wins / n_trades) if n_trades else float("nan"),
        "total_return_pct": float(np.sum(traded)),
        "avg_return_pct": float(np.mean(traded)) if n_trades else float("nan"),
        "return_std_pct": std,
        "sharpe_per_trade": float(np.mean(traded) / std) if std > 0 else float("nan"),
        "equity_curve_pct": list(np.cumsum(traded)) if n_trades else [],
    }


def aggregate(
    anchors: np.ndarray,
    actuals: np.ndarray,
    preds: np.ndarray,
    eval_horizon: int | None = None,
    cost_pct: float = 0.0,
) -> dict:
    """Aggregate metrics across all walk-forward windows.

    Parameters
    ----------
    anchors : (W,) last observed close for each window.
    actuals : (W, H) realized future closes.
    preds   : (W, H) predicted future closes.
    eval_horizon : 1-based horizon step used for the headline directional /
        strategy numbers. Defaults to the last step (the full pred_len).
    cost_pct : round-trip cost in percent applied to the naive strategy.
    """
    anchors = np.asarray(anchors, dtype=float)
    actuals = np.asarray(actuals, dtype=float)
    preds = np.asarray(preds, dtype=float)
    if actuals.shape != preds.shape:
        raise ValueError(f"actuals {actuals.shape} and preds {preds.shape} must match.")
    n_windows, horizon = actuals.shape
    h = horizon if eval_horizon is None else int(eval_horizon)
    if not (1 <= h <= horizon):
        raise ValueError(f"eval_horizon must be in [1, {horizon}], got {h}.")
    hi = h - 1  # 0-based index

    overall = point_errors(actuals.reshape(-1), preds.reshape(-1))

    per_horizon = []
    for k in range(horizon):
        pe = point_errors(actuals[:, k], preds[:, k])
        pe["dir_acc"] = directional_accuracy(anchors, actuals[:, k], preds[:, k])
        pe["horizon"] = k + 1
        per_horizon.append(pe)

    headline_dir = directional_accuracy(anchors, actuals[:, hi], preds[:, hi])
    strat = naive_strategy(anchors, actuals[:, hi], preds[:, hi], cost_pct=cost_pct)

    return {
        "n_windows": int(n_windows),
        "horizon": int(horizon),
        "eval_horizon": int(h),
        "point_errors_overall": overall,
        "directional_accuracy_at_eval_horizon": headline_dir,
        "per_horizon": per_horizon,
        "naive_strategy": strat,
    }


def format_report(summary: dict, symbol: str, timeframe: str) -> str:
    """Render the aggregate() dict as a readable text block."""
    pe = summary["point_errors_overall"]
    strat = summary["naive_strategy"]
    lines = [
        "=" * 64,
        f" Kronos accuracy backtest  -  {symbol} {timeframe}",
        "=" * 64,
        f" windows evaluated : {summary['n_windows']}",
        f" forecast horizon  : {summary['horizon']} bars (eval at bar {summary['eval_horizon']})",
        "-" * 64,
        " Close-path point accuracy (all horizons pooled):",
        f"   MAE  : {pe['mae']:.5f}",
        f"   RMSE : {pe['rmse']:.5f}",
        f"   MAPE : {pe['mape_pct']:.3f} %",
        "-" * 64,
        f" Directional accuracy @ bar {summary['eval_horizon']} : "
        f"{summary['directional_accuracy_at_eval_horizon'] * 100:.2f} %   (50% = coin flip)",
        "-" * 64,
        " Naive direction-only strategy (NOT investment advice):",
        f"   trades        : {strat['n_trades']}",
        f"   hit rate      : {strat['hit_rate'] * 100:.2f} %" if strat["n_trades"] else "   hit rate      : n/a",
        f"   total return  : {strat['total_return_pct']:.3f} %",
        f"   avg / trade   : {strat['avg_return_pct']:.4f} %" if strat["n_trades"] else "   avg / trade   : n/a",
        f"   sharpe/trade  : {strat['sharpe_per_trade']:.3f}",
        "-" * 64,
        " Directional accuracy by horizon:",
    ]
    for ph in summary["per_horizon"]:
        lines.append(f"   bar {ph['horizon']:>3} : {ph['dir_acc'] * 100:5.1f} %   (RMSE {ph['rmse']:.5f})")
    lines.append("=" * 64)
    return "\n".join(lines)
