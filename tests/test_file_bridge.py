"""
Exercise the file bridge between Python and ``KronosTrader.mq5``.

The EA itself is MQL5 and only runs inside a terminal, so the test stands a
small Python stand-in in its place: it follows the same contract - read the
signal, move to the target net position, publish a status file - which is
enough to catch the things that actually break the bridge (a torn read, a
missed confirmation, a flip booked as one fill instead of two, a server clock
that is not UTC).

Run directly - no pytest needed:

    python -m tests.test_file_bridge
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_backtest.trader import BridgeError, FileSignalBroker  # noqa: E402


class FakeEA:
    """Stand-in for KronosTrader.mq5, with the same file contract.

    ``server_offset`` mimics a broker whose server clock is not UTC, which is
    the normal case and the reason signals carry a server-time stamp.
    """

    def __init__(self, signal_path, status_path, symbol="XAUUSD", price=4000.0,
                 server_offset=3 * 3600, trade_mode="DEMO", autotrading=1):
        self.signal_path = signal_path
        self.status_path = status_path
        self.symbol = symbol
        self.price = price
        self.server_offset = server_offset
        self.trade_mode = trade_mode
        self.autotrading = autotrading
        self.equity = 10_000.0
        self.side = 0
        self.volume = 0.0
        self.entry = 0.0
        self.entry_time = 0
        self.last_id = -1
        self.last_action = "none"
        self.close_signal = -1
        self.close_price = 0.0
        self.close_volume = 0.0
        self.close_profit = 0.0
        self.close_known = 1
        self.close_entry = 0.0
        self.close_opened = 0
        self.open_signal = -1
        self.open_price = 0.0
        self.open_volume = 0.0
        self.error = ""
        self.contract_size = 100.0
        self.max_age = 1800
        self.fail_open = False      # close succeeds, the open half is rejected
        self.volume_cap = 0.10      # mirrors InpMaxVolume
        self.write_status()

    # --- the EA's own clock ---------------------------------------------
    def now(self) -> int:
        return int(time.time() + self.server_offset)

    def write_status(self):
        tmp = self.status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(
                f"time={self.now()}\nsymbol={self.symbol}\naccount=5001234\n"
                f"server=Broker-Demo\ntrade_mode={self.trade_mode}\ncurrency=USD\n"
                f"balance={self.equity:.2f}\nequity={self.equity:.2f}\nmargin_free=9000.00\n"
                f"autotrading={self.autotrading}\nblocked=0\n"
                f"volume_min=0.0100\nvolume_step=0.0100\nvolume_max=100.0000\n"
                f"volume_cap={self.volume_cap:.4f}\n"
                f"pos_side={self.side}\npos_volume={self.volume:.2f}\n"
                f"pos_price={self.entry:.5f}\npos_time={self.entry_time}\npos_profit=0.00\n"
                f"last_signal_id={self.last_id}\nlast_action={self.last_action}\n"
                f"last_time={self.now()}\n"
                f"close_signal_id={self.close_signal}\nclose_price={self.close_price:.5f}\n"
                f"close_volume={self.close_volume:.2f}\nclose_profit={self.close_profit:.2f}\n"
                f"close_profit_known={self.close_known}\n"
                f"close_entry={self.close_entry:.5f}\nclose_opened={self.close_opened}\n"
                f"open_signal_id={self.open_signal}\nopen_price={self.open_price:.5f}\n"
                f"open_volume={self.open_volume:.2f}\nerror={self.error}\n"
            )
        os.replace(tmp, self.status_path)

    def poll(self):
        """One EA timer tick: reconcile the position to the signal's target."""
        if not os.path.exists(self.signal_path):
            return
        with open(self.signal_path, encoding="utf-8") as fh:
            rows = [r.strip() for r in fh if r.strip() and not r.startswith("id,")]
        if not rows:
            return
        sid, stamp, symbol, target, volume = rows[-1].split(",")
        sid, stamp, target, volume = int(sid), int(stamp), int(target), float(volume)
        if symbol != self.symbol:
            self.error = f"signal is for {symbol}"
            self.write_status()
            return
        if self.now() - stamp > self.max_age:
            self.error = f"signal {sid} is {self.now() - stamp}s old; ignored"
            self.write_status()
            return
        self.last_id = sid
        if self.side == target:
            self.error = ""
            self.write_status()
            return

        if self.side != 0:                      # close first, always
            self.close_profit = self.side * (self.price - self.entry) * self.volume * self.contract_size
            self.close_entry, self.close_opened = self.entry, self.entry_time
            self.close_signal, self.close_price = sid, self.price
            self.close_volume = self.volume
            self.equity += self.close_profit
            self.last_action = "close"
            self.side, self.volume, self.entry = 0, 0.0, 0.0
            self.error = ""
            self.write_status()                 # publish the close on its own
        if target != 0:
            if self.fail_open:                  # a requote / market-closed reject
                self.error = "open failed: 10018 market closed"
                self.write_status()
                return
            self.side, self.volume = target, volume
            self.entry, self.entry_time = self.price, self.now()
            self.open_signal, self.open_price = sid, self.price
            self.open_volume = volume
            self.last_action = "open_long" if target > 0 else "open_short"
        self.error = ""
        self.write_status()


def _broker(tmp, ea, **kw):
    return FileSignalBroker(ea.signal_path, ea.status_path, "XAUUSD",
                            contract_size=100.0, wait_sec=5.0, poll_sec=0.01, **kw)


def _drive(ea, fn):
    """Run ``fn`` while the fake EA polls in the background, like a live terminal."""
    import threading
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            ea.poll()
            time.sleep(0.01)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        return fn()
    finally:
        stop.set()
        t.join(timeout=1)


def test_open_flip_and_close():
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        b = _broker(tmp, ea).connect()
        assert b.initial_equity == 10_000.0

        def run():
            fill = b.market_order("XAUUSD", 1, 0.01)
            assert fill.action == "open_long", fill.action
            assert b.get_position("XAUUSD").side == 1

            ea.price = 4010.0          # market moves in our favour
            flip = b.market_order("XAUUSD", -1, 0.01)
            assert flip.action == "open_short", flip.action

            ea.price = 4005.0
            closed = b.close_position("XAUUSD")
            assert closed.action == "close"
            assert b.get_position("XAUUSD").is_flat

        _drive(ea, run)

        actions = [f.action for f in b.fills]
        # the flip must book BOTH halves, or the trade log loses a round trip
        assert actions == ["open_long", "close", "open_short", "close"], actions
        closes = [f for f in b.fills if f.action == "close"]
        assert abs(closes[0].realized_pnl - 10.0) < 1e-6, closes[0].realized_pnl   # +10 * 0.01 * 100
        assert abs(closes[1].realized_pnl - 5.0) < 1e-6, closes[1].realized_pnl    # short 4010->4005
        assert abs(b.account_equity() - 10_015.0) < 1e-6
        print("  ok: open / flip / close booked as four fills with broker PnL")


def test_target_is_idempotent():
    """Re-sending the position we already hold must not open a second one."""
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        b = _broker(tmp, ea).connect()
        _drive(ea, lambda: b.market_order("XAUUSD", 1, 0.01))
        again = b.market_order("XAUUSD", 1, 0.01)      # no EA poll needed: short-circuits
        assert again.action == "hold", again.action
        assert len([f for f in b.fills if f.action.startswith("open")]) == 1
        print("  ok: repeating a target is a no-op")


def test_non_utc_server_clock():
    """A server hours away from UTC must not make every signal look stale."""
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"),
                    server_offset=7 * 3600)
        ea.max_age = 60          # tight window: a raw UTC stamp would be rejected
        b = _broker(tmp, ea).connect()
        _drive(ea, lambda: b.market_order("XAUUSD", -1, 0.01))
        assert b.get_position("XAUUSD").side == -1, "signal was rejected as stale"
        print("  ok: stamps are sent in the server's time base")


def test_refuses_real_account_and_autotrading_off():
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"),
                    trade_mode="REAL")
        try:
            _broker(tmp, ea).connect()
            raise AssertionError("a REAL account must be refused")
        except PermissionError:
            pass
        _broker(tmp, ea, allow_real=True).connect()      # explicit override works

        ea2 = FakeEA(os.path.join(tmp, "s2.csv"), os.path.join(tmp, "t2.csv"), autotrading=0)
        try:
            _broker(tmp, ea2).connect()
            raise AssertionError("AutoTrading off must be refused")
        except BridgeError:
            pass
        print("  ok: refuses real accounts and a terminal with AutoTrading off")


def test_missing_and_stale_status():
    with tempfile.TemporaryDirectory() as tmp:
        b = FileSignalBroker(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "nope.csv"),
                             "XAUUSD")
        try:
            b.connect()
            raise AssertionError("a missing status file must be refused")
        except BridgeError as exc:
            assert "no status file" in str(exc)

        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        old = time.time() - 600
        os.utime(ea.status_path, (old, old))
        try:
            _broker(tmp, ea).connect()
            raise AssertionError("a stale status file must be refused")
        except BridgeError as exc:
            assert "looks stopped" in str(exc)
        print("  ok: a missing or stale status file stops the run")


def test_timeout_when_ea_never_acts():
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        b = _broker(tmp, ea)
        b.wait_sec = 0.3
        b.connect()
        try:
            b.market_order("XAUUSD", 1, 0.01)        # nothing polls the signal
            raise AssertionError("must time out when the EA never responds")
        except BridgeError as exc:
            assert "did not reach position" in str(exc)
        print("  ok: an EA that never acts times out instead of hanging")


def test_close_is_booked_even_when_the_open_half_fails():
    """A flip where the broker rejects the new leg still moved real money."""
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        b = _broker(tmp, ea)
        b.wait_sec = 1.0
        b.connect()
        _drive(ea, lambda: b.market_order("XAUUSD", 1, 0.01))

        ea.price = 3990.0          # the long is now 10 points under water
        ea.fail_open = True        # close succeeds, the short is rejected

        def flip():
            try:
                b.market_order("XAUUSD", -1, 0.01)
                raise AssertionError("expected the rejected open to raise")
            except BridgeError as exc:
                assert "close WAS recorded" in str(exc), str(exc)

        _drive(ea, flip)

        closes = [f for f in b.fills if f.action == "close"]
        assert len(closes) == 1, f"the executed close was lost ({len(closes)} booked)"
        assert abs(closes[0].realized_pnl - (-10.0)) < 1e-6, closes[0].realized_pnl
        assert b.get_position("XAUUSD").is_flat
        print("  ok: a losing close is logged even when the open half is rejected")


def test_signal_ids_continue_from_the_ea():
    """A restart must not reuse ids the EA has already marked as handled."""
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        first = _broker(tmp, ea).connect()
        _drive(ea, lambda: first.market_order("XAUUSD", 1, 0.01))
        assert ea.last_id == 1, ea.last_id

        restarted = _broker(tmp, ea).connect()          # fresh process, same EA
        assert restarted._next_id == ea.last_id + 1, restarted._next_id

        def go():
            ea.price = 4020.0
            return restarted.close_position("XAUUSD")

        fill = _drive(ea, go)
        assert fill is not None and fill.action == "close", fill
        print("  ok: a restarted run continues the EA's id sequence")


def test_unknown_close_profit_is_not_recorded_as_zero():
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        b = _broker(tmp, ea).connect()
        _drive(ea, lambda: b.market_order("XAUUSD", 1, 0.01))
        ea.close_known = 0                      # EA could not read deal history
        ea.price = 4050.0
        _drive(ea, lambda: b.close_position("XAUUSD"))
        pnl = [f for f in b.fills if f.action == "close"][-1].realized_pnl
        assert pnl != 0.0 and pnl != pnl, f"unknown PnL booked as {pnl}"   # NaN
        print("  ok: an unreadable close is NaN, never a fake break-even")


def test_volume_is_resolved_against_the_symbol_rules():
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        b = _broker(tmp, ea).connect()

        assert b.resolve_volume(0.03) == 0.03
        assert b.resolve_volume(0.014) == 0.01          # snapped to the step, with a notice

        for bad, why in ((0.001, "below"), (0.5, "exceeds")):
            try:
                b.resolve_volume(bad)
                raise AssertionError(f"{bad} lot should have been refused ({why})")
            except BridgeError as exc:
                assert why in str(exc), str(exc)
        print("  ok: an unplaceable lot size is refused, never silently resized")


def test_request_flat_cancels_a_pending_target():
    """A run that dies must not leave a 'go long' signal the EA still honours."""
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        b = _broker(tmp, ea).connect()
        b.wait_sec = 0.2
        try:
            b.market_order("XAUUSD", 1, 0.01)      # nothing polls it; times out
        except BridgeError:
            pass
        assert open(ea.signal_path).read().strip().endswith(",1,0.0100"), "expected a long target"

        b.request_flat()                            # what shutdown does on the way out
        assert open(ea.signal_path).read().strip().endswith(",0,0.0000")
        ea.poll()
        assert ea.side == 0, "the EA opened a position from the stale target"
        print("  ok: a flat target is published so a dead run leaves nothing pending")


def test_equity_holds_through_an_unreadable_poll():
    with tempfile.TemporaryDirectory() as tmp:
        ea = FakeEA(os.path.join(tmp, "sig.csv"), os.path.join(tmp, "st.csv"))
        b = _broker(tmp, ea).connect()
        assert b.account_equity() == 10_000.0
        os.remove(ea.status_path)
        held = b.account_equity()
        assert held == 10_000.0, f"equity became {held} on one unreadable poll"
        print("  ok: one unreadable poll holds equity instead of logging NaN")


TESTS = [test_open_flip_and_close, test_target_is_idempotent, test_non_utc_server_clock,
         test_refuses_real_account_and_autotrading_off, test_missing_and_stale_status,
         test_timeout_when_ea_never_acts,
         test_close_is_booked_even_when_the_open_half_fails,
         test_signal_ids_continue_from_the_ea,
         test_unknown_close_profit_is_not_recorded_as_zero,
         test_equity_holds_through_an_unreadable_poll,
         test_request_flat_cancels_a_pending_target,
         test_volume_is_resolved_against_the_symbol_rules]

if __name__ == "__main__":
    for t in TESTS:
        print(t.__name__)
        t()
    print(f"\n{len(TESTS)} bridge tests passed")
