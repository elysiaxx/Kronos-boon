"""
Broker abstractions for the Kronos forward-test / demo trading loop.

Three interchangeable brokers share one interface so the live loop code is
identical whether it is paper-trading or sending real orders to a demo
account:

* ``PaperBroker``      - fully in-memory simulation. No MT5 needed, works on
  any OS. Used for dry-runs / CSV replay and for validating the loop logic.
* ``MT5Broker``        - sends market orders to a running MetaTrader 5
  terminal via ``order_send``. Needs the ``MetaTrader5`` package, which ships
  for Windows only.
* ``FileSignalBroker`` - writes the wanted position to a file that the
  ``KronosTrader.mq5`` expert advisor reads and executes, then reads the
  account and fill details back from the EA's status file. The terminal does
  the trading, so this places **real orders from macOS**, where the Python
  package does not exist.

Only a single net position per symbol is supported (flat / long / short),
which keeps the forward test easy to reason about.

Safety: ``MT5Broker`` refuses to attach to a **real-money** account unless
``allow_real=True`` is passed explicitly. The default assumes a demo account,
matching the intended use of this tool.
"""

from __future__ import annotations

import math
import os
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Position:
    """Net position in one symbol. ``side`` is +1 long, -1 short, 0 flat."""
    side: int = 0
    volume: float = 0.0
    entry_price: float = 0.0
    entry_time: Optional[object] = None
    ticket: Optional[int] = None

    @property
    def is_flat(self) -> bool:
        return self.side == 0 or self.volume == 0.0


@dataclass
class Fill:
    """Record of one executed market order (for the trade log).

    A ``close`` fill carries the entry it closed, so a round trip can be
    reconstructed from the fill list alone.
    """
    time: object
    symbol: str
    action: str          # 'open_long' / 'open_short' / 'close' / 'hold'
    volume: float
    price: float
    realized_pnl: float = 0.0
    comment: str = ""
    side: int = 0                      # +1 long, -1 short (the position's side)
    entry_price: float = 0.0           # on a close: where the position was opened
    entry_time: Optional[object] = None


class PaperBroker:
    """In-memory broker. PnL is ``side * (exit - entry) * volume * contract_size``
    in account-currency units (approximate; ``contract_size`` lets you scale it
    for instruments like XAUUSD where 1 lot = 100 oz).

    ``spread`` is the bid/ask gap in price units (XAUUSD is typically 0.15-0.40
    USD on a demo account). Every fill is moved half a spread against the
    trade - buys pay the ask, sells hit the bid - so a round trip costs one
    full spread. Leaving it at 0 makes a forward test look better than any
    broker could ever fill it, which is why the reports call that out.
    """

    def __init__(self, initial_cash: float = 10_000.0, contract_size: float = 1.0,
                 spread: float = 0.0):
        self.initial_cash = float(initial_cash)
        self.contract_size = float(contract_size)
        self.spread = float(spread)
        self.realized_pnl = 0.0
        self.spread_paid = 0.0
        self.position = Position()
        self.fills: list[Fill] = []
        self._last_price = None

    def _exec_price(self, side: int, price: float) -> float:
        """Price actually filled: buys lift the ask, sells hit the bid."""
        return float(price) + side * self.spread / 2.0

    # --- interface -------------------------------------------------------
    def account_equity(self) -> float:
        unrealized = 0.0
        if not self.position.is_flat and self._last_price is not None:
            unrealized = self._pnl(self.position, self._last_price)
        return self.initial_cash + self.realized_pnl + unrealized

    def get_position(self, symbol: str) -> Position:
        return self.position

    def mark(self, price: float):
        """Update the last known price (for equity mark-to-market)."""
        self._last_price = float(price)

    def market_order(self, symbol: str, side: int, volume: float, price: float, time=None) -> Fill:
        """Open (or flip into) a position. Closes an opposite position first."""
        if side not in (1, -1):
            raise ValueError("side must be +1 (long) or -1 (short)")
        self.mark(price)
        if not self.position.is_flat and self.position.side != side:
            self.close_position(symbol, price, time=time)
        if self.position.is_flat:
            exec_price = self._exec_price(side, price)
            self.spread_paid += abs(exec_price - float(price)) * float(volume) * self.contract_size
            self.position = Position(side=side, volume=float(volume),
                                     entry_price=exec_price, entry_time=time)
            fill = Fill(time, symbol, "open_long" if side == 1 else "open_short",
                        volume, exec_price, side=side)
            self.fills.append(fill)
            return fill
        # same-direction signal while already in position: hold, no new fill
        return Fill(time, symbol, "hold", 0.0, price)

    def close_position(self, symbol: str, price: float, time=None) -> Optional[Fill]:
        if self.position.is_flat:
            return None
        self.mark(price)
        pos = self.position
        exec_price = self._exec_price(-pos.side, price)   # exiting trades the other way
        self.spread_paid += abs(exec_price - float(price)) * pos.volume * self.contract_size
        pnl = self._pnl(pos, exec_price)
        self.realized_pnl += pnl
        fill = Fill(time, symbol, "close", pos.volume, exec_price, realized_pnl=pnl,
                    side=pos.side, entry_price=pos.entry_price, entry_time=pos.entry_time)
        self.fills.append(fill)
        self.position = Position()
        return fill

    # --- helpers ---------------------------------------------------------
    def _pnl(self, pos: Position, price: float) -> float:
        return pos.side * (price - pos.entry_price) * pos.volume * self.contract_size


class MT5Broker:
    """Send market orders to a MetaTrader 5 terminal (Windows only).

    Reuses an existing terminal session; pass credentials to drive the login.
    Refuses real-money accounts unless ``allow_real=True``.
    """

    def __init__(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        terminal_path: Optional[str] = None,
        magic: int = 20250826,
        deviation: int = 20,
        allow_real: bool = False,
    ):
        self.login = login
        self.password = password
        self.server = server
        self.terminal_path = terminal_path
        self.magic = magic
        self.deviation = deviation
        self.allow_real = allow_real
        self._mt5 = None
        # Mirrors PaperBroker / FileSignalBroker: without this the fills CSV
        # comes out empty on Windows and the P&L report has nothing to show.
        self.fills: list[Fill] = []

    def connect(self):
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "The 'MetaTrader5' package is required for live trading and is "
                "Windows-only. Install it on the machine running the terminal, "
                "or use PaperBroker (--mode paper)."
            ) from exc

        kwargs = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        if self.login is not None:
            kwargs.update(login=int(self.login), password=self.password, server=self.server)
        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            raise ConnectionError(f"mt5.initialize() failed: ({code}) {msg}")

        info = mt5.account_info()
        if info is None:
            raise ConnectionError("mt5.account_info() returned None; not logged in.")
        # 0 = demo, 1 = contest, 2 = real
        if info.trade_mode == 2 and not self.allow_real:
            mt5.shutdown()
            raise PermissionError(
                f"Refusing to trade on a REAL account (login={info.login}, "
                f"server={info.server}). This tool is meant for demo accounts. "
                "Pass allow_real=True / --allow-real only if you truly intend to "
                "trade real money."
            )
        mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(info.trade_mode, str(info.trade_mode))
        print(f"[mt5] connected: login={info.login} server={info.server} "
              f"mode={mode} balance={info.balance} {info.currency}")
        self._mt5 = mt5
        return self

    def _pick_filling(self, symbol: str):
        mt5 = self._mt5
        info = mt5.symbol_info(symbol)
        # Use the symbol's declared filling policy; fall back to IOC.
        allowed = getattr(info, "filling_mode", 0)
        if allowed & 1:   # SYMBOL_FILLING_FOK
            return mt5.ORDER_FILLING_FOK
        if allowed & 2:   # SYMBOL_FILLING_IOC
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def normalize_volume(self, symbol: str, volume: float) -> float:
        info = self._mt5.symbol_info(symbol)
        step = getattr(info, "volume_step", 0.01) or 0.01
        vmin = getattr(info, "volume_min", step)
        vmax = getattr(info, "volume_max", 1e9)
        vol = max(vmin, min(vmax, volume))
        # round down to the nearest step
        vol = round(round(vol / step) * step, 8)
        return max(vmin, vol)

    def account_equity(self) -> float:
        return float(self._mt5.account_info().equity)

    def get_position(self, symbol: str) -> Position:
        positions = self._mt5.positions_get(symbol=symbol)
        if not positions:
            return Position()
        p = positions[0]
        side = 1 if p.type == self._mt5.POSITION_TYPE_BUY else -1
        return Position(side=side, volume=p.volume, entry_price=p.price_open, ticket=p.ticket)

    def mark(self, price: float):  # no-op; MT5 marks to market itself
        pass

    def _send(self, symbol: str, order_type, volume: float, position_ticket: Optional[int], comment: str):
        mt5 = self._mt5
        if not mt5.symbol_info(symbol).visible:
            mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._pick_filling(symbol),
        }
        if position_ticket is not None:
            request["position"] = position_ticket
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            rc = getattr(result, "retcode", None)
            raise RuntimeError(f"order_send failed (retcode={rc}): {getattr(result, 'comment', mt5.last_error())}")
        return result

    def market_order(self, symbol: str, side: int, volume: float, price: float = None, time=None) -> Fill:
        mt5 = self._mt5
        volume = self.normalize_volume(symbol, volume)
        current = self.get_position(symbol)
        if not current.is_flat and current.side != side:
            self.close_position(symbol, price, time=time)
            current = self.get_position(symbol)
        if current.is_flat:
            otype = mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL
            res = self._send(symbol, otype, volume, None, "kronos_open")
            fill = Fill(time, symbol, "open_long" if side == 1 else "open_short",
                        volume, res.price, side=side)
            self.fills.append(fill)
            return fill
        return Fill(time, symbol, "hold", 0.0, 0.0)

    def close_position(self, symbol: str, price: float = None, time=None) -> Optional[Fill]:
        mt5 = self._mt5
        pos = self.get_position(symbol)
        if pos.is_flat:
            return None
        otype = mt5.ORDER_TYPE_SELL if pos.side == 1 else mt5.ORDER_TYPE_BUY
        res = self._send(symbol, otype, pos.volume, pos.ticket, "kronos_close")
        fill = Fill(time, symbol, "close", pos.volume, res.price,
                    realized_pnl=self._position_profit(pos.ticket),
                    side=pos.side, entry_price=pos.entry_price, entry_time=pos.entry_time)
        self.fills.append(fill)
        return fill

    def _position_profit(self, position_ticket) -> float:
        """Realized PnL of a finished position, summed over all of its deals.

        Summing every deal of the position includes the ENTRY's commission, not
        just the exit's. NaN when the history cannot be read - recording a real
        round trip as 0.00 would quietly pass as a break-even trade.
        """
        try:
            deals = self._mt5.history_deals_get(position=position_ticket)
        except Exception:
            deals = None
        if not deals:
            return float("nan")
        return float(sum(d.profit + d.swap + d.commission for d in deals))

    def shutdown(self):
        if self._mt5 is not None:
            self._mt5.shutdown()
            self._mt5 = None


class BridgeError(RuntimeError):
    """The EA side of the file bridge is missing, stale, or refusing to trade."""


class FileSignalBroker:
    """Place real orders through ``KronosTrader.mq5`` via two files.

    Python writes the **target net position** (+1 long, -1 short, 0 flat) to
    the signal file; the EA moves the account to that target and publishes the
    account, position and last fill to the status file. Targets rather than
    orders means a signal that is read twice, or re-read after a restart, can
    never double up a position.

    Both files live in the terminal's ``Common\\Files`` sandbox, which native
    macOS Python can read and write even though MT5 itself runs under Wine.

    Safety: ``connect()`` refuses a REAL account unless ``allow_real=True``
    (the EA refuses independently, so both sides have to be overridden), and
    refuses to start at all if the EA is not running or AutoTrading is off.
    """

    #: how fresh the EA's status file has to be, in seconds
    STALE_AFTER = 120.0

    def __init__(self, signal_path: str, status_path: str, symbol: str,
                 contract_size: float = 1.0, wait_sec: float = 20.0,
                 poll_sec: float = 0.5, allow_real: bool = False):
        self.signal_path = signal_path
        self.status_path = status_path
        self.symbol = symbol
        self.contract_size = float(contract_size)
        self.wait_sec = float(wait_sec)
        self.poll_sec = float(poll_sec)
        self.allow_real = allow_real
        self.fills: list[Fill] = []
        self.initial_equity: Optional[float] = None
        self._next_id = 1
        self._last_equity: Optional[float] = None
        # Server clock minus ours. MT5 server time is usually not UTC, and the
        # EA ages signals against TimeCurrent(), so stamps have to be sent in
        # the server's time base or every signal looks stale on arrival.
        self._server_offset = 0.0

    # --- status file -----------------------------------------------------
    def _read_status(self, require_fresh: bool = True) -> dict:
        if not os.path.exists(self.status_path):
            raise BridgeError(
                f"no status file at {self.status_path}\n"
                "Attach KronosTrader.mq5 to a chart of this symbol and make sure "
                "InpStatusFile / InpUseCommon match the paths passed here."
            )
        age = _time.time() - os.path.getmtime(self.status_path)
        if require_fresh and age > self.STALE_AFTER:
            raise BridgeError(
                f"status file is {age:.0f}s old - KronosTrader.mq5 looks stopped. "
                "Check the EA is still on the chart and the terminal is running."
            )
        # The EA writes to a temp file and moves it into place, so a read here
        # is either the previous file or the new one, never a torn one.
        out = {}
        with open(self.status_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "=" in line:
                    key, _, value = line.partition("=")
                    out[key.strip()] = value.strip()
        server_now = self._num(out, "time")
        if server_now > 0:
            self._server_offset = server_now - _time.time()
        return out

    @staticmethod
    def _num(status: dict, key: str, default: float = 0.0) -> float:
        try:
            return float(status.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _server_time(status: dict, key: str):
        """Read an epoch field the EA wrote. These are MT5 *server* times, the
        same base as the bar timestamps, so they are not converted."""
        try:
            epoch = int(float(status.get(key, 0) or 0))
        except (TypeError, ValueError):
            return None
        return datetime.utcfromtimestamp(epoch) if epoch > 0 else None

    # --- interface -------------------------------------------------------
    def connect(self):
        status = self._read_status()
        if status.get("blocked") == "1":
            raise BridgeError(f"KronosTrader.mq5 is refusing to trade: {status.get('error', '')}")
        mode = (status.get("trade_mode") or "UNKNOWN").upper()
        if mode == "REAL" and not self.allow_real:
            raise PermissionError(
                f"Refusing to trade on a REAL account (login={status.get('account')}, "
                f"server={status.get('server')}). This tool is meant for demo accounts. "
                "Pass --allow_real only if you truly intend to trade real money."
            )
        if status.get("autotrading") != "1":
            raise BridgeError(
                "AutoTrading is off in the terminal, so the EA cannot place orders. "
                "Turn on the AutoTrading toolbar button and allow algo trading for the EA."
            )
        if status.get("symbol") and status["symbol"] != self.symbol:
            raise BridgeError(
                f"the EA is on {status['symbol']} but this run trades {self.symbol}; "
                "attach KronosTrader.mq5 to the right chart."
            )
        # The EA dedupes signals by exact id and remembers the last one it saw.
        # Restarting Python's counter at 1 would make it ignore this run's first
        # orders as already-handled, silently - so continue its sequence.
        seen = int(self._num(status, "last_signal_id", -1))
        if seen >= self._next_id:
            self._next_id = seen + 1
            print(f"[bridge] continuing signal ids from {self._next_id} "
                  f"(the EA last saw {seen})")
        self.initial_equity = self._num(status, "equity")
        self._last_equity = self.initial_equity
        print(f"[bridge] EA connected: account={status.get('account')} "
              f"server={status.get('server')} mode={mode} "
              f"equity={self.initial_equity:.2f} {status.get('currency', '')}")
        return self

    def account_equity(self) -> float:
        try:
            equity = self._num(self._read_status(), "equity", float("nan"))
            if math.isfinite(equity):
                self._last_equity = equity
                return equity
        except BridgeError:
            pass
        # Do not kill a multi-day run over one unreadable poll, and do not write
        # a NaN into the equity curve either - hold the last known value.
        return self._last_equity if self._last_equity is not None else float("nan")

    def get_position(self, symbol: str) -> Position:
        status = self._read_status()
        side = int(self._num(status, "pos_side"))
        if side == 0:
            return Position()
        return Position(side=side, volume=self._num(status, "pos_volume"),
                        entry_price=self._num(status, "pos_price"),
                        entry_time=self._server_time(status, "pos_time"))

    def mark(self, price):
        pass  # the terminal marks to market itself

    def market_order(self, symbol: str, side: int, volume: float, price=None, time=None) -> Fill:
        if side not in (1, -1):
            raise ValueError("side must be +1 (long) or -1 (short)")
        return self._goto(side, volume, time) or Fill(time, symbol, "hold", 0.0, 0.0)

    def close_position(self, symbol: str, price=None, time=None) -> Optional[Fill]:
        return self._goto(0, 0.0, time)

    def shutdown(self):
        pass

    # --- the handshake ---------------------------------------------------
    def _write_signal(self, target: int, volume: float) -> int:
        """Publish the wanted position. Atomic, so the EA never reads a torn row."""
        signal_id = self._next_id
        self._next_id += 1
        tmp = self.signal_path + ".tmp"
        stamp = int(_time.time() + self._server_offset)
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write("id,time,symbol,target,volume\n")
            fh.write(f"{signal_id},{stamp},{self.symbol},{target},{volume:.2f}\n")
        os.replace(tmp, self.signal_path)
        return signal_id

    def _book_close(self, before: Position, status: dict, when) -> Fill:
        """Record a round trip the EA has reported closing.

        Called the moment the EA stamps the close with our signal id - before
        any raise. The money has already moved on the account; if the open half
        of a flip then fails, or the wait times out, the close must still reach
        the trade log or the measured P&L silently drops a losing trade.
        """
        known = status.get("close_profit_known") == "1"
        fill = Fill(
            self._server_time(status, "last_time") or when,
            self.symbol, "close",
            self._num(status, "close_volume") or before.volume,
            self._num(status, "close_price"),
            realized_pnl=self._num(status, "close_profit") if known else float("nan"),
            side=before.side,
            entry_price=self._num(status, "close_entry") or before.entry_price,
            entry_time=self._server_time(status, "close_opened") or before.entry_time,
        )
        if not known:
            print("[bridge] the EA could not read the deal history for this close, "
                  "so its realized P&L is recorded as unknown rather than as zero",
                  flush=True)
        self.fills.append(fill)
        return fill

    def _goto(self, target: int, volume: float, when) -> Optional[Fill]:
        """Ask the EA for a net position and wait until the account shows it."""
        before = self.get_position(self.symbol)
        if before.side == target:
            return None                       # already there; nothing to execute

        signal_id = self._write_signal(target, volume if target else before.volume)
        deadline = _time.time() + self.wait_sec
        status, closed, last_error, reached = None, None, "", False

        while _time.time() < deadline:
            _time.sleep(self.poll_sec)
            try:
                status = self._read_status()
            except BridgeError:
                continue
            # Book the close as soon as the EA says it belongs to this signal.
            if closed is None and before.side != 0 and \
                    int(self._num(status, "close_signal_id", -1)) == signal_id:
                closed = self._book_close(before, status, when)
            if status.get("error"):
                last_error = status["error"]
            if int(self._num(status, "pos_side")) == target:
                reached = True
                break
            # An error is not final while the EA still has retries left, so it
            # is remembered rather than raised - aborting here used to kill the
            # run while the EA went on to open a position nobody was watching.

        if not reached:
            detail = f": {last_error}" if last_error else ""
            booked = " (its close WAS recorded)" if closed is not None else ""
            raise BridgeError(
                f"EA did not reach position {target:+d} within {self.wait_sec:.0f}s "
                f"(signal {signal_id}){detail}{booked}. Check the terminal: "
                "AutoTrading on, market open, and no position left behind."
            )

        if closed is None and before.side != 0:
            closed = self._book_close(before, status, when)
        if target == 0:
            return closed

        fill = Fill(self._server_time(status, "last_time") or when, self.symbol,
                    "open_long" if target == 1 else "open_short",
                    self._num(status, "open_volume") or self._num(status, "pos_volume") or volume,
                    self._num(status, "open_price") or self._num(status, "pos_price"),
                    side=target)
        self.fills.append(fill)
        return fill
