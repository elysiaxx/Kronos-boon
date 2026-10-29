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

from mt5_backtest.data_source import CSVDataSource, MT5DataSource, timeframe_minutes


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

    Every read is a full HTTP download, so two things keep it civil:

    * a cache - the loop polls every few seconds, but a 15-minute bar cannot
      change that fast. Without it a 24h run downloads ~17,000 times and gets
      itself rate-limited, which then shows up as empty responses.
    * a right-sized window - asking for 60 days to serve 400 bars moves about
      six times more data than the run can use.
    """

    name = "yahoo"
    can_trade_live = False

    #: Kronos timeframe -> yfinance interval
    _INTERVALS = {"M1": "1m", "M5": "5m", "M15": "15m",
                  "M30": "30m", "H1": "1h", "D1": "1d"}
    #: how far back Yahoo will serve each interval
    _MAX_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "1h": 730, "1d": 3650}

    def __init__(self, ticker: str, timeframe: str, symbol: str = None,
                 period: str = None, min_refresh_sec: float = 60.0):
        if timeframe not in self._INTERVALS:
            raise ValueError(f"YahooFeed does not support timeframe {timeframe}; "
                             f"pick one of {sorted(self._INTERVALS)}.")
        self.ticker = ticker
        self.timeframe = timeframe
        self.symbol = symbol or ticker
        self.interval = self._INTERVALS[timeframe]
        self.period = period                  # None => sized from the request
        self.min_refresh_sec = float(min_refresh_sec)
        self._cache = None
        self._cache_at = 0.0

    def _period_for(self, count: int) -> str:
        """Smallest window that comfortably covers ``count`` closed bars."""
        if self.period:
            return self.period
        tf_min = timeframe_minutes(self.timeframe)
        # Gold futures trade ~23h a day, 5 days a week; double it for holidays
        # and half sessions, and never ask for less than 10 days.
        trading_days = (count * tf_min) / (23 * 60.0)
        days = max(10, int(trading_days * (7 / 5.0) * 2) + 1)
        return f"{min(days, self._MAX_DAYS.get(self.interval, 60))}d"

    def latest_closed_bars(self, count: int) -> pd.DataFrame:
        from mt5_backtest.fetch_gold_csv import to_kronos_frame

        now = time.time()
        if self._cache is None or now - self._cache_at >= self.min_refresh_sec:
            from mt5_backtest.fetch_gold_csv import download

            try:
                raw = download(self.ticker, self.interval, self._period_for(count))
            except Exception as exc:
                if self._cache is not None:
                    # Serve the last good frame; the bar gate will simply wait.
                    return self._slice(self._cache, count)
                raise FeedError(f"Yahoo download failed: {exc}") from exc
            df = to_kronos_frame(raw)
            if len(df) < 2:
                raise FeedError(f"Yahoo returned {len(df)} usable bars for {self.ticker}.")
            # The most recent intraday row is the bar still forming; drop it.
            self._cache = df.iloc[:-1].reset_index(drop=True)
            self._cache_at = now
        return self._slice(self._cache, count)

    @staticmethod
    def _slice(df: pd.DataFrame, count: int) -> pd.DataFrame:
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
    """Poll ``feed`` until it serves a bar STRICTLY NEWER than ``last_bar_time``.

    Strictly newer, not merely different. A feed can serve stale data for a
    while - Yahoo load-balances across nodes whose freshness differs, and the
    MT5 bars file can be re-read mid-rewrite - and accepting "different" would
    let an older bar, or one already traded, through. In a forward test that
    books the same round trip twice and invents the profit, so time is only
    ever allowed to move forward.

    Returns ``(bars, bar_time)``, or ``(None, last_bar_time)`` if ``deadline``
    (a ``time.time()`` value) passed first. Transient feed errors and stale
    reads are reported and retried rather than killing a multi-day run.
    """
    warned_stale = None
    while True:
        try:
            bars = feed.latest_closed_bars(count)
            bar_time = bars["timestamps"].iloc[-1]
            if last_bar_time is None or bar_time > last_bar_time:
                return bars, bar_time
            if bar_time < last_bar_time and warned_stale != bar_time:
                # Report once per distinct stale value, not once per poll.
                warned_stale = bar_time
                print(f"[feed] stale read: {bar_time} is older than the last bar "
                      f"{last_bar_time}; ignoring and waiting for a newer one", flush=True)
        except FeedError as exc:
            print(f"[feed] {exc}", flush=True)
        if deadline is not None and time.time() >= deadline:
            return None, last_bar_time
        time.sleep(poll_sec)
