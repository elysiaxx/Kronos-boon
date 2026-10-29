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
        self.last_price = 0.0
        self.close_profit = 0.0
        self.close_entry = 0.0
        self.close_opened = 0
        self.error = ""
        self.contract_size = 100.0
        self.max_age = 1800
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
                f"pos_side={self.side}\npos_volume={self.volume:.2f}\n"
                f"pos_price={self.entry:.5f}\npos_time={self.entry_time}\npos_profit=0.00\n"
                f"last_signal_id={self.last_id}\nlast_action={self.last_action}\n"
                f"last_price={self.last_price:.5f}\nlast_time={self.now()}\n"
                f"close_profit={self.close_profit:.2f}\nclose_entry={self.close_entry:.5f}\n"
                f"close_opened={self.close_opened}\nerror={self.error}\n"
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
            self.equity += self.close_profit
            self.last_action, self.last_price = "close", self.price
            self.side, self.volume, self.entry = 0, 0.0, 0.0
        if target != 0:
            self.side, self.volume = target, volume
            self.entry, self.entry_time = self.price, self.now()
            self.last_action = "open_long" if target > 0 else "open_short"
            self.last_price = self.price
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


TESTS = [test_open_flip_and_close, test_target_is_idempotent, test_non_utc_server_clock,
         test_refuses_real_account_and_autotrading_off, test_missing_and_stale_status,
         test_timeout_when_ea_never_acts]

if __name__ == "__main__":
    for t in TESTS:
        print(t.__name__)
        t()
    print(f"\n{len(TESTS)} bridge tests passed")
