"""
Live bar feeds for the forward-test loop.

All three feeds answer the same question - *"what are the last N **closed**
bars right now?"* - so :mod:`mt5_backtest.live_demo` does not care where the
prices come from:

``MT5LiveFeed``
    Reads the running terminal through the ``MetaTrader5`` package. Fastest
    and most accurate, but that package is **Windows-only**.

``FileBridgeFeed``
    Watches the CSV that ``KronosExport.mq5`` writes into MT5's
    ``Common\\Files`` folder. This is how a Mac (where MT5 runs under Wine and
    the Python package is unavailable) still forward-tests against a real
    broker demo feed - the indicator exports, Python reads.

``YahooFeed``
    Downloads recent bars from Yahoo Finance. No MT5 at all, so a forward test
    can start immediately, at the cost of delayed data and no broker spread.

A feed only reports prices. Whether anything is *traded* on them is the
broker's business - see :mod:`mt5_backtest.trader`.
"""

from __future__ import annotations

import os
import time

import pandas as pd

from mt5_backtest.data_source import CSVDataSource, MT5DataSource


class FeedError(RuntimeError):
    """Raised when a feed cannot produce bars right now (usually transient)."""


class MT5LiveFeed:
    """Live bars straight from a running MetaTrader 5 terminal (Windows)."""

    name = "mt5"
    can_trade_live = True

    def __init__(self, symbol: str, timeframe: str, login=None, password=None,
                 server=None, terminal_path=None):
        self.symbol = symbol
        self.timeframe = timeframe
        self._src = MT5DataSource(login=login, password=password, server=server,
                                  terminal_path=terminal_path)
        self._src.connect()

    def latest_closed_bars(self, count: int) -> pd.DataFrame:
        # The terminal always returns the still-forming bar last; drop it.
        df = self._src.fetch(symbol=self.symbol, timeframe=self.timeframe, count=count + 1)
        if len(df) < 2:
            raise FeedError(f"MT5 returned {len(df)} bars for {self.symbol} {self.timeframe}.")
        return df.iloc[:-1].reset_index(drop=True)

    def close(self):
        self._src.shutdown()


class FileBridgeFeed:
    """Live bars via the CSV ``KronosExport.mq5`` writes (works on macOS/Linux).

    The indicator exports **closed** bars only, so nothing is dropped here.
    A half-written file is normal while MT5 rewrites it; that read simply
    fails and the caller retries on the next poll.
    """

    name = "file"
    can_trade_live = False

    def __init__(self, bars_csv: str, symbol: str, timeframe: str):
        self.path = bars_csv
        self.symbol = symbol
        self.timeframe = timeframe
        self._last_good: pd.DataFrame | None = None

    def latest_closed_bars(self, count: int) -> pd.DataFrame:
        if not os.path.exists(self.path):
            raise FeedError(
                f"bars file not found: {self.path}\n"
                "Is KronosExport.mq5 attached to a chart of this symbol/timeframe? "
                "Run `python -m mt5_backtest.mt5_visualize --find_mt5` to locate Common/Files."
            )
        try:
            df = CSVDataSource(self.path).fetch(symbol=self.symbol, timeframe=self.timeframe,
                                                count=count)
        except Exception as exc:  # torn read while MT5 rewrites the file
            if self._last_good is not None:
                return self._last_good
            raise FeedError(f"could not read {self.path}: {exc}") from exc
        if df.empty:
            raise FeedError(f"{self.path} has no usable rows yet.")
        self._last_good = df
        return df

    def close(self):
        pass


class YahooFeed:
    """Live-ish bars from Yahoo Finance - no MT5, no broker account needed.

    Yahoo intraday data lags the market (typically 10-15 minutes for futures)
    and carries no spread, so a forward test on it measures the *forecast*,
    not what a broker would have filled. Good enough to start today; use the
    file bridge once MT5 is set up.
    """

    name = "yahoo"
    can_trade_live = False

    #: Kronos timeframe -> (yfinance interval, how much history to pull)
    _INTERVALS = {
        "M1": ("1m", "7d"), "M5": ("5m", "60d"), "M15": ("15m", "60d"),
        "M30": ("30m", "60d"), "H1": ("1h", "730d"), "D1": ("1d", "10y"),
    }

    def __init__(self, ticker: str, timeframe: str, symbol: str = None, period: str = None):
        if timeframe not in self._INTERVALS:
            raise ValueError(f"YahooFeed does not support timeframe {timeframe}; "
                             f"pick one of {sorted(self._INTERVALS)}.")
        self.ticker = ticker
        self.timeframe = timeframe
        self.symbol = symbol or ticker
        self.interval, default_period = self._INTERVALS[timeframe]
        self.period = period or default_period

    def latest_closed_bars(self, count: int) -> pd.DataFrame:
        from mt5_backtest.fetch_gold_csv import download, to_kronos_frame

        try:
            raw = download(self.ticker, self.interval, self.period)
        except Exception as exc:
            raise FeedError(f"Yahoo download failed: {exc}") from exc
        df = to_kronos_frame(raw)
        if len(df) < 2:
            raise FeedError(f"Yahoo returned {len(df)} usable bars for {self.ticker}.")
        # The most recent intraday row is the bar still forming; drop it.
        df = df.iloc[:-1].reset_index(drop=True)
        return df.iloc[-count:].reset_index(drop=True) if len(df) > count else df

    def close(self):
        pass


def build_feed(args):
    """Construct the feed named by ``args.feed`` (live feeds only)."""
    if args.feed == "mt5":
        return MT5LiveFeed(args.symbol, args.timeframe, login=args.mt5_login,
                           password=args.mt5_password, server=args.mt5_server,
                           terminal_path=args.mt5_path)
    if args.feed == "file":
        if not args.bars_csv:
            raise SystemExit(
                "--feed file requires --bars_csv <path> - the CSV KronosExport.mq5 writes.\n"
                "Locate it with: python -m mt5_backtest.mt5_visualize --find_mt5"
            )
        return FileBridgeFeed(args.bars_csv, args.symbol, args.timeframe)
    if args.feed == "yahoo":
        return YahooFeed(args.yahoo_ticker, args.timeframe, symbol=args.symbol)
    raise SystemExit(f"'{args.feed}' is not a live feed.")


def wait_for_new_bar(feed, count: int, last_bar_time, poll_sec: float, deadline=None):
    """Poll ``feed`` until its newest closed bar differs from ``last_bar_time``.

    Returns ``(bars, bar_time)``, or ``(None, last_bar_time)`` if ``deadline``
    (a ``time.time()`` value) passed first. Transient feed errors are printed
    and retried rather than killing a multi-day run.
    """
    while True:
        try:
            bars = feed.latest_closed_bars(count)
            bar_time = bars["timestamps"].iloc[-1]
            if bar_time != last_bar_time:
                return bars, bar_time
        except FeedError as exc:
            print(f"[feed] {exc}")
        if deadline is not None and time.time() >= deadline:
            return None, last_bar_time
        time.sleep(poll_sec)
