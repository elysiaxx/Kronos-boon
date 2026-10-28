"""
Export a Kronos forecast so it can be drawn ON the MetaTrader 5 chart.

The MetaTrader5 Python API cannot draw on charts, so this script is the
Python half of a bridge:

    this script (Kronos) --> forecast CSV --> KronosForecast.mq5 indicator

It fetches the latest ``lookback`` closed bars, runs Kronos for ``pred_len``
future bars, and writes a CSV the indicator reads. By default the CSV is
written into MT5's *common* files sandbox (``.../Terminal/Common/Files``),
which the indicator opens with the ``FILE_COMMON`` flag — so paths just work
regardless of which terminal instance you run.

CSV format (epoch seconds, MT5 server time; first row = anchor = last real bar):

    time,open,high,low,close
    1730000000,2630.5,2631.0,2630.1,2630.4   <- anchor (last closed bar)
    1730000900,2630.6,2631.3,2630.2,2631.0   <- forecast bar 1
    ...

Run once, or ``--loop`` to refresh every time a new bar closes so the on-chart
forecast keeps updating.

Examples
--------
    # one-shot, auto-detect the MT5 common Files folder:
    python -m mt5_backtest.mt5_visualize --symbol XAUUSD --timeframe M15 \
        --lookback 400 --pred_len 24

    # keep refreshing each new M15 bar:
    python -m mt5_backtest.mt5_visualize --symbol XAUUSD --timeframe M15 --loop

    # offline test: predict from a CSV and write the forecast to a chosen file
    python -m mt5_backtest.mt5_visualize --feed replay --csv xauusd_m15.csv \
        --symbol XAUUSD --timeframe M15 --out ./kronos_forecast.csv
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mt5_backtest.data_source import CSVDataSource, MT5DataSource, timeframe_minutes  # noqa: E402
from mt5_backtest.backtest import load_predictor  # noqa: E402

PRICE_COLS = ["open", "high", "low", "close", "volume", "amount"]
_EPOCH = pd.Timestamp("1970-01-01")


def _to_epoch(ts: pd.Timestamp) -> int:
    """Invert pd.to_datetime(unit='s'): naive timestamp -> MT5 server epoch."""
    return int((pd.Timestamp(ts) - _EPOCH) / pd.Timedelta(seconds=1))


def write_forecast_csv(path: str, hist: pd.DataFrame, pred_df: pd.DataFrame, period_sec: int):
    """Write anchor + forecast rows as epoch-timestamped OHLC."""
    anchor = hist.iloc[-1]
    anchor_epoch = _to_epoch(anchor["timestamps"])
    rows = [(anchor_epoch, anchor["open"], anchor["high"], anchor["low"], anchor["close"])]
    for i, (_, r) in enumerate(pred_df.iterrows(), start=1):
        rows.append((anchor_epoch + period_sec * i, r["open"], r["high"], r["low"], r["close"]))

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii", newline="\n") as fh:
        fh.write("time,open,high,low,close\n")
        for t, o, h, l, c in rows:
            fh.write(f"{t},{o:.5f},{h:.5f},{l:.5f},{c:.5f}\n")
    os.replace(tmp, path)  # atomic so the indicator never reads a half-written file
    return path


def resolve_out_path(args, mt5_source: MT5DataSource | None) -> str:
    if args.out:
        return args.out
    # auto-detect MT5 common Files folder via the terminal
    if mt5_source is not None and mt5_source._mt5 is not None:
        info = mt5_source._mt5.terminal_info()
        common = getattr(info, "commondata_path", None)
        if common:
            files_dir = os.path.join(common, "Files")
            os.makedirs(files_dir, exist_ok=True)
            return os.path.join(files_dir, args.filename)
    raise SystemExit(
        "Could not auto-detect the MT5 common Files folder. Pass --out explicitly, "
        "e.g. --out \"%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\kronos_forecast.csv\"."
    )


def predict_once(predictor, df, args, period_sec, out_path):
    hist = df.iloc[-args.lookback:]
    x_ts = hist["timestamps"].reset_index(drop=True)
    step = pd.Timedelta(seconds=period_sec)
    y_ts = pd.Series([hist["timestamps"].iloc[-1] + step * (i + 1) for i in range(args.pred_len)])
    cols = [c for c in PRICE_COLS if c in hist.columns]
    pred_df = predictor.predict(
        df=hist[cols].reset_index(drop=True), x_timestamp=x_ts, y_timestamp=y_ts,
        pred_len=args.pred_len, T=args.T, top_p=args.top_p, top_k=args.top_k,
        sample_count=args.sample_count, verbose=False,
    )
    write_forecast_csv(out_path, hist, pred_df, period_sec)
    print(f"[viz] {hist['timestamps'].iloc[-1]}  wrote {args.pred_len}-bar forecast -> {out_path}")
    return pred_df


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Export a Kronos forecast CSV for the MT5 on-chart indicator.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", default="M15")
    p.add_argument("--lookback", type=int, default=400)
    p.add_argument("--pred_len", type=int, default=24)
    p.add_argument("--model", default="NeoQuasar/Kronos-small")
    p.add_argument("--tokenizer", default=None)
    p.add_argument("--max_context", type=int, default=None)
    p.add_argument("--device", default=None)
    p.add_argument("--T", type=float, default=1.0)
    p.add_argument("--top_p", type=float, default=0.9)
    p.add_argument("--top_k", type=int, default=0)
    p.add_argument("--sample_count", type=int, default=1)
    p.add_argument("--seed", type=int, default=None)
    # feed / output
    p.add_argument("--feed", choices=["mt5", "replay"], default="mt5")
    p.add_argument("--csv", default=None, help="CSV for --feed replay")
    p.add_argument("--out", default=None, help="explicit output CSV path (overrides auto-detect)")
    p.add_argument("--filename", default="kronos_forecast.csv", help="file name inside MT5 Common\\Files")
    # loop
    p.add_argument("--loop", action="store_true", help="refresh on every new closed bar")
    p.add_argument("--poll_sec", type=float, default=5.0)
    p.add_argument("--max_updates", type=int, default=0, help="stop after N writes (0 = unlimited)")
    # mt5 creds
    p.add_argument("--mt5_login", type=int, default=None)
    p.add_argument("--mt5_password", default=None)
    p.add_argument("--mt5_server", default=None)
    p.add_argument("--mt5_path", default=None)
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.feed == "replay" and not args.csv:
        raise SystemExit("--feed replay requires --csv <path>.")

    period_sec = timeframe_minutes(args.timeframe) * 60
    predictor = load_predictor(args.model, args.tokenizer, args.device, args.max_context)
    if args.seed is not None:
        import torch, numpy as np
        np.random.seed(args.seed)
        torch.manual_seed(args.seed)

    if args.feed == "replay":
        src = CSVDataSource(args.csv)
        df = src.fetch(symbol=args.symbol, timeframe=args.timeframe)
        out_path = args.out or os.path.join(".", args.filename)
        predict_once(predictor, df, args, period_sec, out_path)
        print("[viz] replay feed is one-shot; use --feed mt5 --loop for live refresh.")
        return

    # live MT5 feed
    source = MT5DataSource(login=args.mt5_login, password=args.mt5_password,
                           server=args.mt5_server, terminal_path=args.mt5_path)
    source.connect()
    out_path = resolve_out_path(args, source)

    try:
        if not args.loop:
            df = source.fetch(symbol=args.symbol, timeframe=args.timeframe, count=args.lookback + 2)
            df = df.iloc[:-1].reset_index(drop=True)  # drop the forming bar
            predict_once(predictor, df, args, period_sec, out_path)
            return
        print(f"[viz] loop mode: refreshing each new {args.timeframe} bar. Ctrl+C to stop.")
        last_bar_time = None
        updates = 0
        while True:
            df = source.fetch(symbol=args.symbol, timeframe=args.timeframe, count=args.lookback + 2)
            df = df.iloc[:-1].reset_index(drop=True)
            cur = df["timestamps"].iloc[-1]
            if cur != last_bar_time:
                last_bar_time = cur
                predict_once(predictor, df, args, period_sec, out_path)
                updates += 1
                if args.max_updates and updates >= args.max_updates:
                    print("[viz] max_updates reached; stopping.")
                    break
            time.sleep(args.poll_sec)
    except KeyboardInterrupt:
        print("\n[viz] stopped by user.")
    finally:
        source.shutdown()


if __name__ == "__main__":
    main()
