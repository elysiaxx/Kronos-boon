"""
Download years of 1-minute history from HistData.com and resample it.

Yahoo caps intraday history hard - 60 days at 15m - which is only ~170
non-overlapping backtest windows, too few to tell a real edge from luck.
HistData publishes free 1-minute bars per instrument per month, going back
years, so a few minutes of downloading buys an order of magnitude more
windows.

    python -m mt5_backtest.fetch_histdata --pair XAUUSD \
        --from 2022-01 --to 2026-09 --timeframe M15 --out gold_m15_long.csv

Caveats worth knowing before trusting the numbers:

* **Timestamps are US Eastern (EST, no daylight saving)**, not the broker
  server time an MT5 export would carry. Kronos only reads cyclical calendar
  features, so a consistent offset is harmless - but do not mix this file
  with bars from another source in one backtest.
* **Volume is not real.** HistData ships 0 in the volume column for metals
  and FX. Rather than feed the model a column of zeros, each resampled bar
  carries the number of 1-minute bars inside it that actually moved, which is
  the same quantity MT5's ``tick_volume`` approximates. It is a proxy, not a
  measurement.
* Data is per calendar month; a month still in progress downloads partially.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import zipfile
from datetime import datetime

import pandas as pd
import requests

from mt5_backtest import line_buffer_stdout

_PAGE = ("https://www.histdata.com/download-free-forex-historical-data/"
         "?/ascii/1-minute-bar-quotes/{pair_lower}/{year}/{month}")
_POST = "https://www.histdata.com/get.php"

#: Kronos timeframe -> pandas resample rule
_RULES = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
          "H1": "1h", "H4": "4h", "D1": "1D"}


class HistDataError(RuntimeError):
    """A month could not be downloaded or parsed."""


def months_between(start: str, end: str) -> list:
    """['2024-01', ...] inclusive, from two 'YYYY-MM' strings."""
    try:
        a, b = datetime.strptime(start, "%Y-%m"), datetime.strptime(end, "%Y-%m")
    except ValueError as exc:
        raise SystemExit(f"--from/--to must look like 2024-01: {exc}")
    if a > b:
        raise SystemExit(f"--from {start} is after --to {end}.")
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch_month(session: requests.Session, pair: str, year: int, month: int) -> pd.DataFrame:
    """One calendar month of 1-minute bars, as a normalized frame."""
    page_url = _PAGE.format(pair_lower=pair.lower(), year=year, month=month)
    page = session.get(page_url, timeout=60)
    if page.status_code != 200:
        raise HistDataError(f"page {year}-{month:02d} returned HTTP {page.status_code}")
    token = re.search(r'id="tk"\s+value="([^"]+)"', page.text)
    if not token:
        raise HistDataError(f"no download token on the {year}-{month:02d} page "
                            f"(is {pair} published for that month?)")

    resp = session.post(_POST, timeout=120, headers={"Referer": page_url}, data={
        "tk": token.group(1), "date": str(year), "datemonth": f"{year}{month:02d}",
        "platform": "ASCII", "timeframe": "M1", "fxpair": pair.upper(),
    })
    if resp.status_code != 200 or len(resp.content) < 1024:
        raise HistDataError(f"download {year}-{month:02d} returned HTTP "
                            f"{resp.status_code}, {len(resp.content)} bytes")

    try:
        archive = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile as exc:
        raise HistDataError(f"{year}-{month:02d} is not a zip: {exc}") from exc
    names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise HistDataError(f"{year}-{month:02d} archive has no CSV")

    with archive.open(names[0]) as fh:
        df = pd.read_csv(fh, sep=";", header=None,
                         names=["stamp", "open", "high", "low", "close", "volume"])
    df["timestamps"] = pd.to_datetime(df["stamp"], format="%Y%m%d %H%M%S")
    return df.drop(columns=["stamp"])


def resample(m1: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """1-minute bars -> the requested timeframe, in the Kronos CSV schema."""
    rule = _RULES.get(timeframe)
    if rule is None:
        raise SystemExit(f"Unsupported --timeframe {timeframe}; pick one of {sorted(_RULES)}.")

    m1 = m1.sort_values("timestamps").drop_duplicates("timestamps").set_index("timestamps")
    # HistData volume is always 0, so count the minutes that actually moved -
    # the same activity measure MT5's tick_volume stands in for.
    m1["moved"] = (m1["high"] > m1["low"]).astype(int)

    out = m1.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                 "close": "last", "moved": "sum"})
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out.rename(columns={"moved": "volume"}).reset_index()
    out["amount"] = out["volume"] * out[["open", "high", "low", "close"]].mean(axis=1)
    return out[["timestamps", "open", "high", "low", "close", "volume", "amount"]]


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Download years of 1-minute history from HistData and resample it.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--pair", default="XAUUSD", help="instrument as HistData names it")
    p.add_argument("--from", dest="start", required=True, help="first month, e.g. 2022-01")
    p.add_argument("--to", dest="end", required=True, help="last month, e.g. 2026-09")
    p.add_argument("--timeframe", default="M15", help="/".join(sorted(_RULES)))
    p.add_argument("--out", default="histdata.csv")
    p.add_argument("--sleep", type=float, default=1.5,
                   help="pause between months; be polite to a free service")
    args = p.parse_args(argv)
    line_buffer_stdout()

    wanted = months_between(args.start, args.end)
    print(f"[histdata] {args.pair} {args.timeframe}: {len(wanted)} months "
          f"({args.start} -> {args.end})")

    session = requests.Session()
    frames, missing = [], []
    for i, (year, month) in enumerate(wanted, 1):
        try:
            frame = fetch_month(session, args.pair, year, month)
            frames.append(frame)
            print(f"[histdata] {i:3}/{len(wanted)}  {year}-{month:02d}  {len(frame):6} M1 bars")
        except (HistDataError, requests.RequestException) as exc:
            missing.append(f"{year}-{month:02d}")
            print(f"[histdata] {i:3}/{len(wanted)}  {year}-{month:02d}  SKIPPED: {exc}")
        time.sleep(args.sleep)

    if not frames:
        raise SystemExit("Nothing downloaded; check the pair name and the date range.")

    bars = resample(pd.concat(frames, ignore_index=True), args.timeframe)
    bars.to_csv(args.out, index=False)

    span = bars["timestamps"]
    print(f"\n[histdata] wrote {len(bars)} {args.timeframe} bars to {args.out}")
    print(f"[histdata] range {span.iloc[0]} -> {span.iloc[-1]}")
    if missing:
        # Never let a silent gap pass as complete history.
        print(f"[histdata] {len(missing)} month(s) MISSING: {', '.join(missing)}")
    print("[histdata] note: timestamps are US Eastern (no DST); volume is a "
          "moved-minutes proxy, not a real traded volume.")


if __name__ == "__main__":
    main()
