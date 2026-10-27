"""
Turn a Kronos forecast into a trading signal.

Kept deliberately small and broker-agnostic so it can be unit-tested with a
mock predictor. The rule is intentionally simple (this is a forecast-quality
probe, not a production strategy):

    look at the predicted close at ``eval_horizon`` bars ahead; if the
    expected move versus the last observed close exceeds ``threshold_pct``,
    signal long/short, otherwise stay flat.

The threshold filters out tiny, noise-level forecasts so the loop does not
churn trades on every bar.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PRICE_COLS = ["open", "high", "low", "close", "volume", "amount"]


@dataclass
class Signal:
    direction: int          # +1 long, -1 short, 0 flat
    anchor: float           # last observed close
    target: float           # predicted close at eval_horizon
    expected_move_pct: float
    pred_close: list        # full predicted close path
    threshold_pct: float


class SignalGenerator:
    def __init__(self, predictor, pred_len: int, eval_horizon: int | None = None,
                 threshold_pct: float = 0.1, T: float = 1.0, top_p: float = 0.9,
                 top_k: int = 0, sample_count: int = 1):
        self.predictor = predictor
        self.pred_len = pred_len
        self.eval_horizon = eval_horizon or pred_len
        self.threshold_pct = threshold_pct
        self.T = T
        self.top_p = top_p
        self.top_k = top_k
        self.sample_count = sample_count
        if not (1 <= self.eval_horizon <= self.pred_len):
            raise ValueError("eval_horizon must be in [1, pred_len]")

    def generate(self, hist_df, x_timestamp, y_timestamp) -> Signal:
        cols = [c for c in PRICE_COLS if c in hist_df.columns]
        pred_df = self.predictor.predict(
            df=hist_df[cols].reset_index(drop=True),
            x_timestamp=x_timestamp.reset_index(drop=True),
            y_timestamp=y_timestamp.reset_index(drop=True),
            pred_len=self.pred_len,
            T=self.T, top_p=self.top_p, top_k=self.top_k,
            sample_count=self.sample_count, verbose=False,
        )
        anchor = float(hist_df["close"].iloc[-1])
        pred_close = pred_df["close"].to_numpy(dtype=float)
        target = float(pred_close[self.eval_horizon - 1])
        exp_move_pct = (target - anchor) / anchor * 100.0 if anchor else 0.0

        if exp_move_pct > self.threshold_pct:
            direction = 1
        elif exp_move_pct < -self.threshold_pct:
            direction = -1
        else:
            direction = 0

        return Signal(
            direction=direction, anchor=anchor, target=target,
            expected_move_pct=exp_move_pct, pred_close=list(np.asarray(pred_close, dtype=float)),
            threshold_pct=self.threshold_pct,
        )
