"""
Guard the live loop's bar gate.

A forward test is only honest if every decision is made on a bar strictly
newer than the last one it acted on. Real feeds break that: Yahoo serves
different freshness from different nodes, and the MT5 bars file can be read
while the indicator is rewriting it. When a stale bar slips through, the loop
re-trades a bar it already traded and books the profit again - the measured
P&L becomes fiction, which is worse than a crash because it looks fine.

Run directly - no pytest needed:

    python -m tests.test_live_loop
"""

from __future__ import annotations

import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_backtest.live_feed import FeedError, wait_for_new_bar  # noqa: E402


class ScriptedFeed:
    """Serves a canned sequence of newest-closed-bar times, one per poll.

    An entry may be a timestamp string, or ``"ERR"`` to raise FeedError on that
    poll. Once the script runs out the last value repeats, like a feed that has
    simply not produced a new bar yet.
    """

    name = "scripted"

    def __init__(self, script, rows=400):
        self.script = list(script)
        self.rows = rows
        self.polls = 0

    def latest_closed_bars(self, count):
        i = min(self.polls, len(self.script) - 1)
        self.polls += 1
        entry = self.script[i]
        if entry == "ERR":
            raise FeedError("simulated transient feed failure")
        end = pd.Timestamp(entry)
        stamps = pd.date_range(end=end, periods=self.rows, freq="15min")
        return pd.DataFrame({"timestamps": stamps, "close": [1.0] * self.rows})

    def close(self):
        pass


def _drain(script, n, **kw):
    """Pull n bars through the gate, returning what the loop would act on."""
    feed = ScriptedFeed(script)
    out, last = [], None
    for _ in range(n):
        bars, last = wait_for_new_bar(feed, 400, last, poll_sec=0.001,
                                      deadline=time.time() + 2, **kw)
        if bars is None:
            break
        out.append(last)
    return out


def test_monotonic_feed_passes_everything_through():
    got = _drain(["2026-10-29 03:30", "2026-10-29 03:45", "2026-10-29 04:00"], 3)
    assert [str(t) for t in got] == ["2026-10-29 03:30:00", "2026-10-29 03:45:00",
                                     "2026-10-29 04:00:00"], got
    print("  ok: a well-behaved feed passes straight through")


def test_stale_bar_is_never_acted_on():
    """The exact live failure: the feed falls back a day, then recovers."""
    script = ["2026-10-29 03:30",   # acted on
              "2026-10-28 03:45",   # stale node - must NOT be acted on
              "2026-10-28 03:45",
              "2026-10-29 03:30",   # same bar again - must NOT be acted on
              "2026-10-29 03:45"]   # genuinely newer
    got = [str(t) for t in _drain(script, 2)]
    assert got == ["2026-10-29 03:30:00", "2026-10-29 03:45:00"], got
    assert "2026-10-28 03:45:00" not in got, "a stale bar was traded"
    print("  ok: a feed that falls backwards never produces a second decision")


def test_same_bar_is_offered_only_once():
    got = _drain(["2026-10-29 04:00"] * 6, 2)
    assert len(got) == 1, f"the same bar was acted on {len(got)} times"
    print("  ok: repeating the newest bar does not re-trigger")


def test_deadline_stops_waiting():
    feed = ScriptedFeed(["2026-10-29 04:00"])
    last = pd.Timestamp("2026-10-29 04:00")          # already acted on
    bars, back = wait_for_new_bar(feed, 400, last, poll_sec=0.01,
                                  deadline=time.time() + 0.1)
    assert bars is None and back == last, (bars, back)
    print("  ok: a run that reaches its deadline stops instead of hanging")


def test_transient_errors_are_retried():
    got = _drain(["ERR", "ERR", "2026-10-29 04:00"], 1)
    assert [str(t) for t in got] == ["2026-10-29 04:00:00"], got
    print("  ok: a feed hiccup is retried, not fatal")


# --------------------------------------------------------------------------- #
# YahooFeed: a multi-day run must not hammer the API, nor die when it answers
# badly once. The two faults compound - the download storm is what provokes the
# rate-limited empty response in the first place.
# --------------------------------------------------------------------------- #

def _fake_yahoo(monkey_calls, fail_after=None):
    """Patch fetch_gold_csv.download with a counter; optionally start failing."""
    import pandas as pd
    from mt5_backtest import fetch_gold_csv

    def fake(ticker, interval, period):
        monkey_calls.append(period)
        if fail_after is not None and len(monkey_calls) > fail_after:
            raise fetch_gold_csv.DownloadError("simulated rate limit: no data returned")
        idx = pd.date_range("2026-10-20", periods=500, freq="15min")
        return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0,
                             "Close": 1.0, "Volume": 1}, index=idx)

    original = fetch_gold_csv.download
    fetch_gold_csv.download = fake
    return original


def _restore_yahoo(original):
    from mt5_backtest import fetch_gold_csv
    fetch_gold_csv.download = original


def test_download_error_is_catchable():
    """SystemExit inherits BaseException, so `except Exception` would miss it."""
    from mt5_backtest.fetch_gold_csv import DownloadError
    assert issubclass(DownloadError, Exception)
    assert not issubclass(DownloadError, SystemExit)
    print("  ok: a failed download raises a catchable exception, not SystemExit")


def test_yahoo_feed_caches_between_polls():
    from mt5_backtest.live_feed import YahooFeed
    calls = []
    original = _fake_yahoo(calls)
    try:
        feed = YahooFeed("GC=F", "M15", min_refresh_sec=3600)
        for _ in range(20):
            feed.latest_closed_bars(400)
        assert len(calls) == 1, f"polled the API {len(calls)} times for one bar"
        # and the window must be sized to the request, not the 60-day maximum
        assert calls[0] == "13d", calls[0]
    finally:
        _restore_yahoo(original)
    print("  ok: 20 polls cost one download, over a right-sized window")


def test_yahoo_outage_serves_cache_instead_of_dying():
    from mt5_backtest.live_feed import YahooFeed
    calls = []
    original = _fake_yahoo(calls, fail_after=1)
    try:
        feed = YahooFeed("GC=F", "M15", min_refresh_sec=0)   # refresh every call
        first = feed.latest_closed_bars(400)
        later = feed.latest_closed_bars(400)                 # this one fails
        assert len(later) == len(first)
        assert later["timestamps"].iloc[-1] == first["timestamps"].iloc[-1]
    finally:
        _restore_yahoo(original)
    print("  ok: a rate-limited response falls back to the last good frame")


def test_yahoo_first_call_failure_is_a_feed_error():
    """With no cache yet there is nothing to serve - it must be retryable."""
    from mt5_backtest.live_feed import YahooFeed
    calls = []
    original = _fake_yahoo(calls, fail_after=0)
    try:
        feed = YahooFeed("GC=F", "M15", min_refresh_sec=0)
        try:
            feed.latest_closed_bars(400)
            raise AssertionError("expected a FeedError")
        except FeedError as exc:
            assert "download failed" in str(exc)
    finally:
        _restore_yahoo(original)
    print("  ok: a cold-start failure is a FeedError the loop retries")


TESTS = [test_monotonic_feed_passes_everything_through,
         test_stale_bar_is_never_acted_on,
         test_same_bar_is_offered_only_once,
         test_deadline_stops_waiting,
         test_transient_errors_are_retried,
         test_download_error_is_catchable,
         test_yahoo_feed_caches_between_polls,
         test_yahoo_outage_serves_cache_instead_of_dying,
         test_yahoo_first_call_failure_is_a_feed_error]

if __name__ == "__main__":
    for t in TESTS:
        print(t.__name__)
        t()
    print(f"\n{len(TESTS)} live-loop tests passed")
