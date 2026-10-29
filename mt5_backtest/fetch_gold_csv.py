"""
Download gold OHLCV bars into a Kronos-ready CSV, without MetaTrader 5.

Handy on macOS (where the MetaTrader5 Python package does not run) or anywhere
you just want data fast: it pulls bars from Yahoo Finance via ``yfinance`` and
writes the ``timestamps,open,high,low,close,volume,amount`` format that
``backtest.py --source csv`` and the visualizer expect.

Default is COMEX gold futures ``GC=F`` (a close proxy for XAUUSD spot) at 15m.
Yahoo limits intraday history: 15m bars go back ~60 days, 1h ~730 days.

    pip install yfinance

    python -m mt5_backtest.fetch_gold_csv --out gold_m15.csv
    python -m mt5_backtest.fetch_gold_csv --ticker XAUUSD=X --interval 15m --out xau.csv
    python -m mt5_backtest.fetch_gold_csv --interval 1h --period 2y --out gold_h1.csv

Then:

    python -m mt5_backtest.backtest --source csv --csv gold_m15.csv \
        --symbol XAUUSD --timeframe M15 --lookback 400 --pred_len 24 --n_windows 50
"""

from __future__ import annotations

import argparse

import pandas as pd

OHLCV_COLUMNS = ["timestamps", "open", "high", "low", "close", "volume", "amount"]


def to_kronos_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Map a yfinance OHLCV frame to the Kronos CSV schema."""
    df = df.copy()
    # yfinance can return MultiIndex columns ((field, ticker)); flatten to field.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise ValueError(f"Downloaded data is missing columns {missing}. Got {list(df.columns)}.")

    out = pd.DataFrame()
    out["timestamps"] = pd.to_datetime(df.index).tz_localize(None)
    for c in ("open", "high", "low", "close"):
        out[c] = pd.to_numeric(df[c].values, errors="coerce")
    out["volume"] = pd.to_numeric(df["volume"].values, errors="coerce") if "volume" in df.columns else 0.0
    out["volume"] = out["volume"].fillna(0.0)
    out["amount"] = out["volume"] * out[["open", "high", "low", "close"]].mean(axis=1)

    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    out = out.sort_values("timestamps").drop_duplicates("timestamps").reset_index(drop=True)
    if out.empty:
        raise ValueError("No usable rows after cleaning the download.")
    return out[OHLCV_COLUMNS]


def download(ticker: str, interval: str, period: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit("yfinance is required: pip install yfinance") from exc
    raw = yf.download(ticker, interval=interval, period=period,
                      auto_adjust=False, progress=False)
    if raw is None or len(raw) == 0:
        raise SystemExit(
            f"No data returned for {ticker} ({interval}, {period}). "
            "Check the ticker and that the interval/period is allowed by Yahoo "
            "(15m -> max ~60d, 1h -> max ~730d)."
        )
    return raw


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Download gold OHLCV into a Kronos-ready CSV (via yfinance).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--ticker", default="GC=F", help="Yahoo ticker (GC=F futures, XAUUSD=X spot)")
    p.add_argument("--interval", default="15m", help="1m/5m/15m/30m/60m/1h/1d ...")
    p.add_argument("--period", default="60d", help="e.g. 60d (15m max), 2y (1h max ~730d)")
    p.add_argument("--out", required=True, help="output CSV path")
    args = p.parse_args(argv)

    raw = download(args.ticker, args.interval, args.period)
    df = to_kronos_frame(raw)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} bars to {args.out}")
    print(f"Range: {df['timestamps'].iloc[0]} -> {df['timestamps'].iloc[-1]}")
    print("Tip: run  python -m mt5_backtest.backtest --source csv --csv "
          f"{args.out} --symbol XAUUSD --timeframe "
          f"{'M15' if args.interval in ('15m',) else 'H1' if args.interval in ('1h','60m') else 'M5'}")


if __name__ == "__main__":
    main()
