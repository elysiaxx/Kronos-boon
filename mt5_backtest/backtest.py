"""
Walk-forward accuracy backtest for Kronos on MT5 (or CSV) data.

What it does
------------
1. Pull a long stretch of bars for one symbol/timeframe (e.g. XAUUSD M15)
   from a running MT5 terminal, or from a CSV export.
2. Slide a window across history. At each step, feed the last ``lookback``
   bars to Kronos, ask for ``pred_len`` future bars, and hold out the real
   future bars that MT5 already knows.
3. Compare predicted vs. realized closes and report point-accuracy,
   directional accuracy, and a naive direction-only strategy. Save a JSON
   report, PNG charts, and an interactive HTML report that draws every
   forecast window against what actually happened (open it in a browser -
   it is the readable version of the numbers printed to the terminal).

This measures how well the pre-trained model forecasts the chosen instrument;
no look-ahead is possible because each forecast only ever sees bars strictly
before the window it is scored on.

Run ``python -m mt5_backtest.backtest --help`` for all options, or see the
README for copy-paste commands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

# Make the repo root importable so `from model import ...` works regardless of
# where this script is launched from.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mt5_backtest import line_buffer_stdout  # noqa: E402
from mt5_backtest.data_source import (  # noqa: E402
    CSVDataSource,
    MT5DataSource,
    timeframe_minutes,
)
from mt5_backtest import metrics  # noqa: E402

# Known tokenizer pairing + context length for the public Kronos checkpoints.
_MODEL_DEFAULTS = {
    "NeoQuasar/Kronos-mini": ("NeoQuasar/Kronos-Tokenizer-2k", 2048),
    "NeoQuasar/Kronos-small": ("NeoQuasar/Kronos-Tokenizer-base", 512),
    "NeoQuasar/Kronos-base": ("NeoQuasar/Kronos-Tokenizer-base", 512),
}


def load_predictor(model_name: str, tokenizer_name: str | None, device: str | None, max_context: int | None):
    """Load tokenizer + model and wrap them in a KronosPredictor."""
    from model import Kronos, KronosTokenizer, KronosPredictor

    default_tok, default_ctx = _MODEL_DEFAULTS.get(model_name, (None, 512))
    if tokenizer_name is None:
        tokenizer_name = default_tok
        if tokenizer_name is None:
            raise ValueError(
                f"Unknown model '{model_name}'; please pass --tokenizer explicitly."
            )
    if max_context is None:
        max_context = default_ctx

    print(f"[load] tokenizer = {tokenizer_name}")
    print(f"[load] model     = {model_name}")
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_name)
    model = Kronos.from_pretrained(model_name)
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=max_context)
    print(f"[load] device    = {predictor.device}  max_context = {max_context}")
    return predictor


def build_source(args):
    if args.source == "mt5":
        src = MT5DataSource(
            login=args.mt5_login,
            password=args.mt5_password,
            server=args.mt5_server,
            terminal_path=args.mt5_path,
        )
        src.connect()
        return src
    if args.source == "csv":
        if not args.csv:
            raise SystemExit("--source csv requires --csv <path>")
        return CSVDataSource(args.csv)
    raise SystemExit(f"Unknown source '{args.source}'")


def run_backtest(args) -> dict:
    tf_min = timeframe_minutes(args.timeframe)

    # How many bars we need: the first window's lookback, plus one pred_len of
    # held-out future, plus a stride for every extra window.
    n_bars_needed = args.lookback + args.pred_len + (args.n_windows - 1) * args.step
    # Pull a comfortable margin so weekends / gaps don't starve the last window.
    fetch_count = int(n_bars_needed * 1.2) + args.lookback

    source = build_source(args)
    print(f"[data] fetching ~{fetch_count} bars of {args.symbol} {args.timeframe} from '{args.source}' ...")
    df = source.fetch(symbol=args.symbol, timeframe=args.timeframe, count=fetch_count)
    if hasattr(source, "shutdown"):
        source.shutdown()
    print(f"[data] got {len(df)} bars  ({df['timestamps'].iloc[0]} -> {df['timestamps'].iloc[-1]})")

    if len(df) < args.lookback + args.pred_len:
        raise SystemExit(
            f"Not enough bars ({len(df)}) for lookback={args.lookback} + pred_len={args.pred_len}."
        )

    # Determine the window start indices (walk forward, most recent last).
    last_start = len(df) - (args.lookback + args.pred_len)
    starts = list(range(0, last_start + 1, args.step))
    if len(starts) > args.n_windows:
        starts = starts[-args.n_windows:]  # keep the most recent n_windows
    print(f"[run] evaluating {len(starts)} windows "
          f"(lookback={args.lookback}, pred_len={args.pred_len}, step={args.step})")

    predictor = load_predictor(args.model, args.tokenizer, args.device, args.max_context)

    if args.seed is not None:
        import torch
        np.random.seed(args.seed)
        torch.manual_seed(args.seed)

    anchors, actuals, preds = [], [], []
    windows = []            # per-window detail for the interactive HTML report
    sample_window = None
    sample_pick = len(starts) // 2  # a middle window for the overlay chart

    price_cols = ["open", "high", "low", "close", "volume", "amount"]
    for wi, s in enumerate(starts):
        hist = df.iloc[s : s + args.lookback]
        fut = df.iloc[s + args.lookback : s + args.lookback + args.pred_len]

        x_df = hist[price_cols].reset_index(drop=True)
        x_ts = hist["timestamps"].reset_index(drop=True)
        y_ts = fut["timestamps"].reset_index(drop=True)

        pred_df = predictor.predict(
            df=x_df,
            x_timestamp=x_ts,
            y_timestamp=y_ts,
            pred_len=args.pred_len,
            T=args.T,
            top_p=args.top_p,
            top_k=args.top_k,
            sample_count=args.sample_count,
            verbose=False,
        )

        anchor = float(hist["close"].iloc[-1])
        anchors.append(anchor)
        actuals.append(fut["close"].to_numpy(dtype=float))
        preds.append(pred_df["close"].to_numpy(dtype=float))

        tail = max(1, args.hist_tail)
        windows.append({
            "index": wi,
            "start": int(s),
            "anchor": anchor,
            "anchor_ts": str(x_ts.iloc[-1]),
            "hist_ts": [str(t) for t in x_ts.iloc[-tail:]],
            "hist_close": [float(v) for v in hist["close"].to_numpy()[-tail:]],
            "fut_ts": [str(t) for t in y_ts],
            "fut_close": [float(v) for v in fut["close"].to_numpy()],
            "pred_close": [float(v) for v in pred_df["close"].to_numpy()],
        })

        if wi == sample_pick:
            sample_window = {
                "hist_ts": x_ts, "hist_close": hist["close"].to_numpy(dtype=float),
                "fut_ts": y_ts, "fut_close": fut["close"].to_numpy(dtype=float),
                "pred_close": pred_df["close"].to_numpy(dtype=float),
            }
        if (wi + 1) % max(1, len(starts) // 10) == 0 or wi == len(starts) - 1:
            print(f"[run]   window {wi + 1}/{len(starts)} done")

    anchors = np.asarray(anchors)
    actuals = np.vstack(actuals)
    preds = np.vstack(preds)

    eval_h = args.eval_horizon or args.pred_len
    summary = metrics.aggregate(anchors, actuals, preds, eval_horizon=eval_h, cost_pct=args.cost_pct)
    summary["config"] = {
        "symbol": args.symbol, "timeframe": args.timeframe, "timeframe_minutes": tf_min,
        "lookback": args.lookback, "pred_len": args.pred_len, "step": args.step,
        "model": args.model, "tokenizer": args.tokenizer or _MODEL_DEFAULTS.get(args.model, (None,))[0],
        "T": args.T, "top_p": args.top_p, "top_k": args.top_k,
        "sample_count": args.sample_count, "seed": args.seed, "source": args.source,
        "data_start": str(df["timestamps"].iloc[0]), "data_end": str(df["timestamps"].iloc[-1]),
    }

    print()
    print(metrics.format_report(summary, args.symbol, args.timeframe))

    _save_outputs(args, summary, sample_window, windows)
    return summary


def _save_outputs(args, summary, sample_window, windows=None):
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"{args.symbol}_{args.timeframe}_{stamp}"

    json_path = os.path.join(out_dir, f"{tag}_report.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=float)
    print(f"\n[out] report : {json_path}")

    if windows and not args.no_html:
        from mt5_backtest.report_html import build_detail, write_report

        detail = build_detail(summary, windows, args.symbol, args.timeframe)
        detail_path = os.path.join(out_dir, f"{tag}_detail.json")
        with open(detail_path, "w", encoding="utf-8") as fh:
            json.dump(detail, fh, default=float)
        html_path = write_report(detail, os.path.join(out_dir, f"{tag}_report.html"))
        print(f"[out] detail : {detail_path}")
        print(f"[out] HTML   : {html_path}")
        print(f"[out]          ^ open this one - every window drawn + each metric explained")

    if args.no_plots:
        return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        print(f"[out] plotting skipped ({exc})")
        return

    # 1) sample window overlay
    if sample_window is not None:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(range(len(sample_window["hist_close"])), sample_window["hist_close"],
                color="#1f77b4", label="history close")
        n_hist = len(sample_window["hist_close"])
        xf = range(n_hist, n_hist + len(sample_window["fut_close"]))
        ax.plot(xf, sample_window["fut_close"], color="#2ca02c", label="actual future")
        ax.plot(xf, sample_window["pred_close"], color="#d62728", linestyle="--", label="Kronos forecast")
        ax.axvline(n_hist - 0.5, color="grey", linewidth=0.8)
        ax.set_title(f"{args.symbol} {args.timeframe} - sample forecast window")
        ax.set_xlabel("bar index"); ax.set_ylabel("close"); ax.legend(); ax.grid(alpha=0.3)
        p = os.path.join(out_dir, f"{tag}_sample_window.png")
        fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig)
        print(f"[out] chart  : {p}")

    # 2) per-horizon directional accuracy
    horizons = [ph["horizon"] for ph in summary["per_horizon"]]
    diracc = [ph["dir_acc"] * 100 for ph in summary["per_horizon"]]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(horizons, diracc, color="#1f77b4")
    ax.axhline(50, color="red", linestyle="--", linewidth=1, label="coin flip (50%)")
    ax.set_title(f"{args.symbol} {args.timeframe} - directional accuracy by horizon")
    ax.set_xlabel("forecast horizon (bars ahead)"); ax.set_ylabel("dir. accuracy (%)")
    ax.legend(); ax.grid(alpha=0.3)
    p = os.path.join(out_dir, f"{tag}_dir_accuracy.png")
    fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig)
    print(f"[out] chart  : {p}")

    # 3) naive-strategy equity curve
    eq = summary["naive_strategy"].get("equity_curve_pct", [])
    if eq:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(range(1, len(eq) + 1), eq, color="#9467bd")
        ax.axhline(0, color="grey", linewidth=0.8)
        ax.set_title(f"{args.symbol} {args.timeframe} - naive strategy cumulative return")
        ax.set_xlabel("trade #"); ax.set_ylabel("cumulative return (%)"); ax.grid(alpha=0.3)
        p = os.path.join(out_dir, f"{tag}_equity_curve.png")
        fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig)
        print(f"[out] chart  : {p}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Walk-forward accuracy backtest for Kronos on MT5/CSV data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # instrument
    p.add_argument("--symbol", default="XAUUSD", help="MT5 symbol name")
    p.add_argument("--timeframe", default="M15", help="M1/M5/M15/M30/H1/H4/D1/W1")
    # windows
    p.add_argument("--lookback", type=int, default=400, help="history bars fed to the model (<= max_context)")
    p.add_argument("--pred_len", type=int, default=24, help="bars to forecast per window")
    p.add_argument("--n_windows", type=int, default=30, help="number of walk-forward windows (most recent kept)")
    p.add_argument("--step", type=int, default=24, help="bars to advance between windows (==pred_len => non-overlapping)")
    p.add_argument("--eval_horizon", type=int, default=None, help="1-based horizon for headline dir-acc/strategy (default: pred_len)")
    p.add_argument("--cost_pct", type=float, default=0.0, help="round-trip cost (%%) for the naive strategy")
    # model / sampling
    p.add_argument("--model", default="NeoQuasar/Kronos-small", help="HF id or local path")
    p.add_argument("--tokenizer", default=None, help="HF id or local path (auto for known models)")
    p.add_argument("--max_context", type=int, default=None, help="override model context length")
    p.add_argument("--device", default=None, help="cpu / cuda:0 / mps (auto if omitted)")
    p.add_argument("--T", type=float, default=1.0, help="sampling temperature (lower = more deterministic)")
    p.add_argument("--top_p", type=float, default=0.9)
    p.add_argument("--top_k", type=int, default=0)
    p.add_argument("--sample_count", type=int, default=1, help="samples averaged per window (higher = steadier)")
    p.add_argument("--seed", type=int, default=None, help="seed for reproducible sampling")
    # data source
    p.add_argument("--source", choices=["mt5", "csv"], default="mt5")
    p.add_argument("--csv", default=None, help="CSV path when --source csv")
    p.add_argument("--mt5_login", type=int, default=None)
    p.add_argument("--mt5_password", default=None)
    p.add_argument("--mt5_server", default=None)
    p.add_argument("--mt5_path", default=None, help="path to terminal64.exe (optional)")
    # output
    p.add_argument("--out_dir", default=os.path.join(_THIS_DIR, "results"))
    p.add_argument("--no_plots", action="store_true", help="skip the PNG charts")
    p.add_argument("--no_html", action="store_true", help="skip the interactive HTML report")
    p.add_argument("--hist_tail", type=int, default=80,
                   help="history bars kept per window in the HTML report (display only)")
    return p


def main(argv=None):
    line_buffer_stdout()
    args = build_arg_parser().parse_args(argv)
    if args.lookback > (args.max_context or _MODEL_DEFAULTS.get(args.model, (None, 512))[1]):
        print(f"[warn] lookback {args.lookback} exceeds model context; it will be truncated by the predictor.")
    run_backtest(args)


if __name__ == "__main__":
    main()
