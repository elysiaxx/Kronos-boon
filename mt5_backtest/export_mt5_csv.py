"""
Export bars from a running MetaTrader 5 terminal to a Kronos-ready CSV.

Handy when you want to (a) run the backtest on a different machine than the
one with MT5 installed, or (b) keep a fixed dataset so repeated backtests are
comparable. Windows only (needs the MT5 terminal + ``MetaTrader5`` package).

Example
-------
    python -m mt5_backtest.export_mt5_csv --symbol XAUUSD --timeframe M15 \
        --count 8000 --out xauusd_m15.csv

Then on any machine:

    python -m mt5_backtest.backtest --source csv --csv xauusd_m15.csv \
        --symbol XAUUSD --timeframe M15
"""

from __future__ import annotations

import argparse

from mt5_backtest.data_source import MT5DataSource


def main(argv=None):
    p = argparse.ArgumentParser(description="Export MT5 bars to a Kronos-ready CSV.")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", default="M15", help="M1/M5/M15/M30/H1/H4/D1/W1")
    p.add_argument("--count", type=int, default=8000, help="most recent N bars")
    p.add_argument("--out", required=True, help="output CSV path")
    p.add_argument("--mt5_login", type=int, default=None)
    p.add_argument("--mt5_password", default=None)
    p.add_argument("--mt5_server", default=None)
    p.add_argument("--mt5_path", default=None)
    args = p.parse_args(argv)

    src = MT5DataSource(
        login=args.mt5_login,
        password=args.mt5_password,
        server=args.mt5_server,
        terminal_path=args.mt5_path,
    )
    src.connect()
    try:
        df = src.fetch(symbol=args.symbol, timeframe=args.timeframe, count=args.count)
    finally:
        src.shutdown()

    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} bars to {args.out}")
    print(f"Range: {df['timestamps'].iloc[0]} -> {df['timestamps'].iloc[-1]}")


if __name__ == "__main__":
    main()
