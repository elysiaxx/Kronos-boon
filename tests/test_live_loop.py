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


TESTS = [test_monotonic_feed_passes_everything_through,
         test_stale_bar_is_never_acted_on,
         test_same_bar_is_offered_only_once,
         test_deadline_stops_waiting,
         test_transient_errors_are_retried]

if __name__ == "__main__":
    for t in TESTS:
        print(t.__name__)
        t()
    print(f"\n{len(TESTS)} live-loop tests passed")
