"""
Data sources for the Kronos MT5 accuracy backtest.

Two interchangeable sources produce the SAME normalized OHLCV frame that
Kronos expects, so the backtest code never needs to know where the bars
came from:

    columns = ['timestamps', 'open', 'high', 'low', 'close', 'volume', 'amount']
    (sorted by time ascending, no gaps re-filled, no NaNs)

* ``MT5DataSource``  - pulls bars live from a running MetaTrader 5 terminal
                       (Windows only; needs the ``MetaTrader5`` package and a
                       logged-in demo/real account).
* ``CSVDataSource``  - reads bars from a CSV exported from MT5 (or any of the
                       CSVs already in this repo). Works on any OS, which is
                       how the pipeline is validated without a terminal.

Volume handling: forex / metals symbols usually have no real traded volume,
so ``real_volume`` is 0 and we fall back to ``tick_volume``. ``amount`` is
left for Kronos to derive (it fills ``amount = volume * mean(OHLC)`` when the
column is absent), which keeps behaviour identical to the library default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

# Canonical schema every source must return.
OHLCV_COLUMNS = ["timestamps", "open", "high", "low", "close", "volume", "amount"]

# String timeframe -> (MetaTrader5 constant name, minutes-per-bar).
# The constant name is resolved lazily so this module imports fine without MT5.
_TIMEFRAMES = {
    "M1": ("TIMEFRAME_M1", 1),
    "M5": ("TIMEFRAME_M5", 5),
    "M15": ("TIMEFRAME_M15", 15),
    "M30": ("TIMEFRAME_M30", 30),
    "H1": ("TIMEFRAME_H1", 60),
    "H4": ("TIMEFRAME_H4", 240),
    "D1": ("TIMEFRAME_D1", 1440),
    "W1": ("TIMEFRAME_W1", 10080),
}


def timeframe_minutes(timeframe: str) -> int:
    """Return the number of minutes in one bar of ``timeframe`` (e.g. 'M15' -> 15)."""
    tf = timeframe.upper()
    if tf not in _TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Choose one of {list(_TIMEFRAMES)}.")
    return _TIMEFRAMES[tf][1]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Sort, type, validate and trim a frame down to OHLCV_COLUMNS."""
    df = df.copy()
    df["timestamps"] = pd.to_datetime(df["timestamps"])
    df = df.sort_values("timestamps").drop_duplicates("timestamps").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "amount" not in df.columns:
        # Let Kronos derive amount; keep the column present and explicit here.
        df["amount"] = df["volume"] * df[["open", "high", "low", "close"]].mean(axis=1)
    else:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df = df[OHLCV_COLUMNS]
    bad = df[["open", "high", "low", "close", "volume"]].isnull().any(axis=1)
    if bad.any():
        df = df[~bad].reset_index(drop=True)
    if df.empty:
        raise ValueError("Data source produced no usable rows after cleaning.")
    return df


class CSVDataSource:
    """Read normalized bars from a CSV file (MT5 export or a repo CSV).

    Accepts common column aliases so an MT5 "Save As CSV" dump, an exported
    history, or the project's own CSVs all load without manual editing.
    """

    _ALIASES = {
        "timestamps": "timestamps",
        "timestamp": "timestamps",
        "time": "timestamps",
        "date": "timestamps",
        "datetime": "timestamps",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "tick_volume": "volume",
        "tickvol": "volume",
        "vol": "volume",
        "real_volume": "volume",
        "amount": "amount",
        "turnover": "amount",
    }

    def __init__(self, path: str):
        self.path = path

    def fetch(
        self,
        symbol: Optional[str] = None,       # unused; kept for interface parity
        timeframe: Optional[str] = None,    # unused
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        count: Optional[int] = None,
    ) -> pd.DataFrame:
        raw = pd.read_csv(self.path, encoding="utf-8-sig")
        rename = {}
        for col in raw.columns:
            key = col.strip().lower().lstrip("﻿")
            if key in self._ALIASES:
                rename[col] = self._ALIASES[key]
        raw = raw.rename(columns=rename)

        # Several source columns can map to one target (MT5 exports carry both
        # tick_volume and real_volume -> volume). Collapse duplicates, keeping
        # the column with the larger magnitude so a populated real_volume wins
        # but a forex tick_volume survives when real_volume is all zeros.
        if raw.columns.duplicated().any():
            collapsed = {}
            for i, name in enumerate(raw.columns):
                series = raw.iloc[:, i]
                if name not in collapsed:
                    collapsed[name] = series
                else:
                    prev_sum = pd.to_numeric(collapsed[name], errors="coerce").abs().sum()
                    cur_sum = pd.to_numeric(series, errors="coerce").abs().sum()
                    if cur_sum > prev_sum:
                        collapsed[name] = series
            raw = pd.DataFrame(collapsed)

        missing = [c for c in ("open", "high", "low", "close") if c not in raw.columns]
        if missing:
            raise ValueError(f"CSV '{self.path}' is missing required columns {missing}. Found {list(raw.columns)}.")
        if "timestamps" not in raw.columns:
            raw["timestamps"] = pd.date_range("2000-01-01", periods=len(raw), freq="min")
        if "volume" not in raw.columns:
            raw["volume"] = 0.0

        df = _normalize(raw)
        if start is not None:
            df = df[df["timestamps"] >= pd.Timestamp(start)]
        if end is not None:
            df = df[df["timestamps"] <= pd.Timestamp(end)]
        df = df.reset_index(drop=True)
        if count is not None and len(df) > count:
            df = df.iloc[-count:].reset_index(drop=True)
        return df


class MT5DataSource:
    """Pull bars from a running MetaTrader 5 terminal.

    Windows only: the official ``MetaTrader5`` package talks to a local MT5
    terminal over IPC. Launch MT5, log into your demo account, enable
    "Algo Trading", then run the backtest on that machine.

    Credentials are optional: if the terminal is already logged in, a bare
    ``initialize()`` reuses that session. Pass login/password/server to drive
    the login programmatically.
    """

    def __init__(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        terminal_path: Optional[str] = None,
        prefer_real_volume: bool = True,
    ):
        self.login = login
        self.password = password
        self.server = server
        self.terminal_path = terminal_path
        self.prefer_real_volume = prefer_real_volume
        self._mt5 = None

    def connect(self):
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "The 'MetaTrader5' package is required for live MT5 data and is "
                "Windows-only. Install it with 'pip install MetaTrader5' on the "
                "machine running the MT5 terminal, or use CSVDataSource instead."
            ) from exc

        kwargs = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        if self.login is not None:
            kwargs.update(login=int(self.login), password=self.password, server=self.server)

        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            raise ConnectionError(f"mt5.initialize() failed: ({code}) {msg}")
        self._mt5 = mt5
        return self

    def _resolve_timeframe(self, timeframe: str):
        tf = timeframe.upper()
        if tf not in _TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe '{timeframe}'. Choose one of {list(_TIMEFRAMES)}.")
        const_name = _TIMEFRAMES[tf][0]
        return getattr(self._mt5, const_name)

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        count: Optional[int] = None,
    ) -> pd.DataFrame:
        if self._mt5 is None:
            self.connect()
        mt5 = self._mt5
        mt5_tf = self._resolve_timeframe(timeframe)

        # Ensure the symbol is visible in Market Watch, otherwise copy_rates
        # returns nothing for demo accounts that hide most symbols by default.
        info = mt5.symbol_info(symbol)
        if info is None:
            raise ValueError(f"Symbol '{symbol}' not found on this MT5 account/server.")
        if not info.visible:
            if not mt5.symbol_select(symbol, True):
                raise ValueError(f"Could not add symbol '{symbol}' to Market Watch.")

        if count is not None and start is None:
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, int(count))
        else:
            date_from = start or datetime(2000, 1, 1, tzinfo=timezone.utc)
            date_to = end or datetime.now(timezone.utc)
            rates = mt5.copy_rates_range(symbol, mt5_tf, date_from, date_to)

        if rates is None or len(rates) == 0:
            code, msg = mt5.last_error()
            raise ValueError(
                f"No bars returned for {symbol} {timeframe}. MT5 last_error=({code}) {msg}. "
                "Check the symbol name, that history is downloaded, and the date range."
            )

        df = pd.DataFrame(rates)
        df["timestamps"] = pd.to_datetime(df["time"], unit="s")
        if self.prefer_real_volume and "real_volume" in df.columns and df["real_volume"].sum() > 0:
            df["volume"] = df["real_volume"]
        else:
            df["volume"] = df.get("tick_volume", 0.0)
        df = df[["timestamps", "open", "high", "low", "close", "volume"]]
        df = _normalize(df)
        if count is not None and len(df) > count:
            df = df.iloc[-count:].reset_index(drop=True)
        return df

    def shutdown(self):
        if self._mt5 is not None:
            self._mt5.shutdown()
            self._mt5 = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.shutdown()
