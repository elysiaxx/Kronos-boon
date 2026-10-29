"""
Forward-test / demo trading loop for Kronos on MetaTrader 5.

Each time a new bar closes, feed the last ``lookback`` bars to Kronos, turn
the forecast into a long/short/flat signal, and act on it through a broker:

* ``--broker paper`` (default)  simulate fills in memory, place nothing.
* ``--broker file``             write the wanted position to a file that the
                                ``KronosTrader.mq5`` expert advisor executes -
                                real orders from macOS, DEMO by default.
* ``--broker mt5``              send market orders to a running MT5 terminal
                                through the Windows-only Python package.

Bars can come from four feeds (see :mod:`mt5_backtest.live_feed`):

* ``--feed mt5``     live from the terminal (Windows only).
* ``--feed file``    live from the CSV ``KronosExport.mq5`` writes - this is
                     how macOS forward-tests a real broker demo feed.
* ``--feed yahoo``   live-ish from Yahoo Finance; no MT5 or broker needed.
* ``--feed replay``  replay a CSV as if the bars were arriving now (offline).

Replay + paper is a fully offline dry-run of the exact same loop, and is how
the logic is validated without a terminal.

Costs
-----
``--spread`` (price units) moves every paper fill half a spread against the
trade, so a round trip costs one full spread - the dominant cost at M15 on
gold. It defaults to 0, which flatters the result; the reports say so.

Safety
------
* Paper is the default; real orders require ``--broker mt5``.
* ``MT5Broker`` refuses real-money accounts unless ``--allow-real`` is given.
* ``--broker mt5`` is only allowed with ``--feed mt5`` (you cannot send real
  orders against replayed history).

Examples
--------
Offline dry-run on a CSV (no MT5 needed):
    python -m mt5_backtest.live_demo --broker paper --feed replay \
        --csv xauusd_m15.csv --symbol XAUUSD --timeframe M15 \
        --lookback 400 --pred_len 24 --eval_horizon 8 --threshold_pct 0.1

Trade a DEMO account from macOS (EA places the orders):
    python -m mt5_backtest.live_demo --broker file --feed file \
        --bars_csv "$COMMON/kronos_bars.csv" --symbol XAUUSD --timeframe M15 \
        --volume 0.01 --duration_min 1440 --flatten_on_exit

Paper-trade the live market on macOS, via the MT5 file bridge:
    python -m mt5_backtest.live_demo --broker paper --feed file \
        --bars_csv "$COMMON/kronos_bars.csv" --symbol XAUUSD --timeframe M15 \
        --spread 0.30 --duration_min 1440

Paper-trade with no MT5 at all (Yahoo data):
    python -m mt5_backtest.live_demo --broker paper --feed yahoo \
        --yahoo_ticker GC=F --symbol XAUUSD --timeframe M15 --duration_min 480

Trade a DEMO account for real:
    python -m mt5_backtest.live_demo --broker mt5 --feed mt5 \
        --symbol XAUUSD --timeframe M15 --volume 0.01 \
        --mt5_login 123 --mt5_password pw --mt5_server Broker-Demo --max_bars 96
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mt5_backtest.data_source import CSVDataSource, timeframe_minutes  # noqa: E402
from mt5_backtest.live_feed import build_feed, wait_for_new_bar  # noqa: E402
from mt5_backtest.strategy import SignalGenerator  # noqa: E402
from mt5_backtest.trader import PaperBroker, MT5Broker, FileSignalBroker  # noqa: E402
from mt5_backtest.backtest import load_predictor  # noqa: E402

PRICE_COLS = ["open", "high", "low", "close", "volume", "amount"]


class TradeLogger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._fh = open(path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(["bar_time", "anchor", "target", "exp_move_pct",
                           "direction", "action", "fill_price", "equity"])

    def log(self, bar_time, sig, action, fill_price, equity):
        self._w.writerow([bar_time, f"{sig.anchor:.5f}", f"{sig.target:.5f}",
                          f"{sig.expected_move_pct:.4f}", sig.direction, action,
                          "" if fill_price is None else f"{fill_price:.5f}",
                          f"{equity:.2f}"])
        self._fh.flush()

    def close(self):
        self._fh.close()


class FillLogger:
    """Mirror the broker's executed fills to CSV as they happen.

    Written incrementally so a run that is killed (laptop sleeps, terminal
    closed) still leaves a readable record of everything filled so far.
    """

    COLUMNS = ["time", "action", "side", "volume", "price",
               "entry_time", "entry_price", "realized_pnl"]

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._fh = open(path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(self.COLUMNS)
        self._written = 0

    def sync(self, broker):
        """Append any fills the broker has recorded since the last call."""
        fills = getattr(broker, "fills", None)
        if not fills:
            return
        for f in fills[self._written:]:
            if f.action == "hold":
                continue
            self._w.writerow([f.time, f.action, f.side, f"{f.volume:.4f}", f"{f.price:.5f}",
                              f.entry_time if f.entry_time is not None else "",
                              f"{f.entry_price:.5f}" if f.entry_price else "",
                              f"{f.realized_pnl:.5f}"])
        self._written = len(fills)
        self._fh.flush()

    def close(self):
        self._fh.close()


def _act_on_signal(broker, symbol, sig, exec_price, bar_time) -> tuple[str, float | None]:
    """Apply a signal through the broker. Returns (action, fill_price)."""
    pos = broker.get_position(symbol)
    if sig.direction == 0:
        if not pos.is_flat:
            fill = broker.close_position(symbol, exec_price, time=bar_time)
            return "close", getattr(fill, "price", exec_price)
        return "flat", None
    # directional signal
    if pos.is_flat:
        fill = broker.market_order(symbol, sig.direction, _volume_ctx["volume"], exec_price, time=bar_time)
        return fill.action, fill.price
    if pos.side == sig.direction:
        return "hold", None
    # opposite -> flip (market_order closes then opens)
    fill = broker.market_order(symbol, sig.direction, _volume_ctx["volume"], exec_price, time=bar_time)
    return "flip_" + fill.action, fill.price


# small module-level holder so _act_on_signal can see the configured volume
_volume_ctx = {"volume": 0.01}


def run_replay(args, predictor, broker, logger, fills_log):
    src = CSVDataSource(args.csv)
    df = src.fetch(symbol=args.symbol, timeframe=args.timeframe)
    n = len(df)
    if n < args.lookback + 2:
        raise SystemExit(f"CSV has {n} bars; need at least lookback+2 = {args.lookback + 2}.")

    print(f"[replay] {n} bars, iterating from bar {args.lookback} onward")
    steps = 0
    last_bar = df.iloc[-1]
    # decide at close of bar t (history = bars up to t), fill at open of bar t+1
    for t in range(args.lookback - 1, n - 1):
        hist = df.iloc[t - args.lookback + 1 : t + 1]
        x_ts = hist["timestamps"]
        # future timestamps are only needed for their calendar features;
        # synthesize them from the bar cadence so replay needs no future rows.
        step = pd.Timedelta(minutes=timeframe_minutes(args.timeframe))
        y_ts = pd.Series([hist["timestamps"].iloc[-1] + step * (i + 1) for i in range(args.pred_len)])

        sig = siggen.generate(hist, x_ts, y_ts)
        next_bar = df.iloc[t + 1]
        exec_price = float(next_bar["open"])
        broker.mark(exec_price)
        action, fill_price = _act_on_signal(broker, args.symbol, sig, exec_price, next_bar["timestamps"])
        broker.mark(float(next_bar["close"]))
        equity = broker.account_equity()
        logger.log(next_bar["timestamps"], sig, action, fill_price, equity)
        fills_log.sync(broker)
        last_bar = next_bar

        steps += 1
        if steps % max(1, (n // 20)) == 0 or action not in ("hold", "flat"):
            print(f"[replay] {next_bar['timestamps']}  dir={sig.direction:+d} "
                  f"move={sig.expected_move_pct:+.3f}%  {action:12s} equity={equity:.2f}")
        if args.max_bars and steps >= args.max_bars:
            break

    # Flatten at the end so realized PnL is final. Use the bar the loop actually
    # stopped on - closing at the CSV's final bar would peek past --max_bars.
    broker.close_position(args.symbol, float(last_bar["close"]), time=last_bar["timestamps"])
    fills_log.sync(broker)
    return steps


def run_live(args, predictor, broker, logger, fills_log):
    """Decide once per closed bar, from whichever live feed was selected."""
    feed = build_feed(args)
    period = timeframe_minutes(args.timeframe)
    paper = isinstance(broker, PaperBroker)
    print(f"[live] feed={feed.name} {args.symbol} {args.timeframe} (bar = {period} min). "
          f"Ctrl+C to stop.")
    if args.broker == "mt5" and not feed.can_trade_live:
        raise SystemExit(f"--broker mt5 cannot run on the '{feed.name}' feed; it needs --feed mt5.")

    last_bar_time = None
    last_close = None
    steps = 0
    started = time.time()
    deadline = started + args.duration_min * 60 if args.duration_min else None
    try:
        while True:
            bars, bar_time = wait_for_new_bar(feed, args.lookback, last_bar_time,
                                              args.poll_sec, deadline)
            if bars is None:
                print("[live] duration reached; stopping.")
                break
            # Claim the bar before deciding whether we can use it: leaving
            # last_bar_time behind would re-offer this same bar on every poll.
            last_bar_time = bar_time
            if len(bars) < args.lookback:
                print(f"[live] only {len(bars)} bars available, need {args.lookback}; "
                      f"skipping bar {bar_time}", flush=True)
                continue

            hist = bars.iloc[-args.lookback:]
            x_ts = hist["timestamps"]
            stepd = pd.Timedelta(minutes=period)
            y_ts = pd.Series([bar_time + stepd * (i + 1) for i in range(args.pred_len)])
            sig = siggen.generate(hist, x_ts, y_ts)

            # The decision is made as this bar closes, so the close is the last
            # price actually observable at that moment - that is what a paper
            # fill uses. MT5Broker ignores it and fills on the live tick.
            last_close = float(hist["close"].iloc[-1])
            exec_price = last_close if paper else None
            if paper:
                broker.mark(last_close)
            action, fill_price = _act_on_signal(broker, args.symbol, sig, exec_price, bar_time)
            equity = broker.account_equity()
            logger.log(bar_time, sig, action, fill_price, equity)
            fills_log.sync(broker)
            print(f"[live] {bar_time}  close={last_close:.2f}  dir={sig.direction:+d} "
                  f"move={sig.expected_move_pct:+.3f}%  {action:12s} equity={equity:.2f}")

            steps += 1
            if args.max_bars and steps >= args.max_bars:
                print("[live] max_bars reached; stopping.")
                break
    except KeyboardInterrupt:
        print("\n[live] interrupted by user.")
    finally:
        if args.flatten_on_exit:
            # This runs in a finally, so anything raised here would mask the
            # real error - and with a real broker it would also hide that a
            # position is still open.
            try:
                if not broker.get_position(args.symbol).is_flat:
                    broker.close_position(args.symbol, last_close, time=datetime.now())
                    print("[live] position flattened on exit.", flush=True)
            except Exception as exc:
                print(f"[live] COULD NOT FLATTEN ON EXIT: {exc}\n"
                      f"[live] check {args.symbol} in the terminal - a position may "
                      f"still be open.", flush=True)
            finally:
                fills_log.sync(broker)
        feed.close()
    return steps


def _write_session(path: str, session: dict):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(session, fh, indent=2, default=str)


# Module-level signal generator, set up in main() once the predictor is built.
siggen: SignalGenerator = None  # type: ignore


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Kronos forward-test / demo trading loop on MT5.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", default="M15")
    p.add_argument("--lookback", type=int, default=400)
    p.add_argument("--pred_len", type=int, default=24)
    p.add_argument("--eval_horizon", type=int, default=8, help="1-based horizon used for the signal")
    p.add_argument("--threshold_pct", type=float, default=0.1,
                   help="min predicted move (%%) to trade; smaller => trade more")
    # model / sampling
    p.add_argument("--model", default="NeoQuasar/Kronos-small")
    p.add_argument("--tokenizer", default=None)
    p.add_argument("--max_context", type=int, default=None)
    p.add_argument("--device", default=None)
    p.add_argument("--T", type=float, default=1.0)
    p.add_argument("--top_p", type=float, default=0.9)
    p.add_argument("--top_k", type=int, default=0)
    p.add_argument("--sample_count", type=int, default=1)
    p.add_argument("--seed", type=int, default=None)
    # broker / feed
    p.add_argument("--broker", choices=["paper", "file", "mt5"], default="paper",
                   help="paper: simulate. file: KronosTrader.mq5 places real orders "
                        "(works on macOS). mt5: MetaTrader5 package (Windows).")
    p.add_argument("--feed", choices=["mt5", "file", "yahoo", "replay"], default="mt5",
                   help="mt5: terminal (Windows). file: CSV from KronosExport.mq5 (macOS). "
                        "yahoo: Yahoo Finance (no MT5). replay: offline CSV.")
    p.add_argument("--csv", default=None, help="CSV for --feed replay")
    p.add_argument("--bars_csv", default=None,
                   help="bars CSV for --feed file (written by KronosExport.mq5)")
    p.add_argument("--yahoo_ticker", default="GC=F", help="ticker for --feed yahoo")
    p.add_argument("--signal_csv", default=None,
                   help="signal file for --broker file (default: beside --bars_csv)")
    p.add_argument("--status_csv", default=None,
                   help="status file written by KronosTrader.mq5 (default: beside --bars_csv)")
    p.add_argument("--order_wait_sec", type=float, default=20.0,
                   help="how long to wait for the EA to reach the requested position")
    p.add_argument("--volume", type=float, default=0.01, help="lot size per trade")
    p.add_argument("--spread", type=float, default=0.0,
                   help="bid/ask spread in price units for paper fills (XAUUSD demo: ~0.30). "
                        "0 means free trading, which no broker offers.")
    p.add_argument("--initial_cash", type=float, default=10_000.0, help="paper starting equity")
    p.add_argument("--contract_size", type=float, default=100.0,
                   help="paper PnL scaling (XAUUSD: 100 oz/lot)")
    p.add_argument("--allow_real", action="store_true", help="permit a REAL MT5 account (danger)")
    p.add_argument("--mt5_login", type=int, default=None)
    p.add_argument("--mt5_password", default=None)
    p.add_argument("--mt5_server", default=None)
    p.add_argument("--mt5_path", default=None)
    # loop control
    p.add_argument("--max_bars", type=int, default=0, help="stop after N decisions (0 = unlimited)")
    p.add_argument("--duration_min", type=float, default=0.0, help="stop after N minutes (live only)")
    p.add_argument("--poll_sec", type=float, default=5.0, help="live feed poll interval")
    p.add_argument("--flatten_on_exit", action="store_true", help="close open position when stopping (live)")
    p.add_argument("--out_dir", default=os.path.join(_THIS_DIR, "results"))
    p.add_argument("--no_html", action="store_true", help="skip the HTML P&L report")
    return p


def main(argv=None):
    global siggen
    args = build_arg_parser().parse_args(argv)

    # A forward test is normally watched through `| tee run.log`, and a pipe
    # makes stdout block-buffered - the loop would appear to print nothing for
    # hours. Line buffering costs nothing here and keeps the log live.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:  # pragma: no cover - very old Python
        pass

    if args.broker == "mt5" and args.feed != "mt5":
        raise SystemExit(
            f"--broker mt5 requires --feed mt5; '{args.feed}' cannot drive the Python API.\n"
            "On macOS that package does not exist - use --broker file --feed file, "
            "where KronosTrader.mq5 places the orders inside the terminal."
        )
    if args.broker == "file" and args.feed != "file":
        raise SystemExit(
            f"--broker file requires --feed file, not '{args.feed}'.\n"
            "Orders go to your broker, so the prices the signal is built from must "
            "come from the same terminal - not from Yahoo or a replayed CSV."
        )
    if args.feed == "replay" and not args.csv:
        raise SystemExit("--feed replay requires --csv <path>.")
    if args.feed == "file" and not args.bars_csv:
        raise SystemExit(
            "--feed file requires --bars_csv <path> - the CSV KronosExport.mq5 writes.\n"
            "Locate it with: python -m mt5_backtest.mt5_visualize --find_mt5"
        )
    if args.broker == "paper" and args.spread <= 0:
        print("[warn] --spread 0: paper fills pay no bid/ask cost, so the P&L will be "
              "better than any broker could fill. For XAUUSD try --spread 0.30.")

    _volume_ctx["volume"] = args.volume

    predictor = load_predictor(args.model, args.tokenizer, args.device, args.max_context)
    if args.seed is not None:
        import torch, numpy as np
        np.random.seed(args.seed)
        torch.manual_seed(args.seed)

    siggen = SignalGenerator(
        predictor, pred_len=args.pred_len, eval_horizon=args.eval_horizon,
        threshold_pct=args.threshold_pct, T=args.T, top_p=args.top_p,
        top_k=args.top_k, sample_count=args.sample_count,
    )

    currency = ""
    if args.broker == "paper":
        broker = PaperBroker(initial_cash=args.initial_cash, contract_size=args.contract_size,
                             spread=args.spread)
    elif args.broker == "file":
        bars_dir = os.path.dirname(os.path.abspath(args.bars_csv))
        signal_path = args.signal_csv or os.path.join(bars_dir, "kronos_signal.csv")
        status_path = args.status_csv or os.path.join(bars_dir, "kronos_status.csv")
        broker = FileSignalBroker(signal_path, status_path, args.symbol,
                                  contract_size=args.contract_size,
                                  wait_sec=args.order_wait_sec,
                                  allow_real=args.allow_real).connect()
        status = broker._read_status()
        currency = status.get("currency", "")
        args.initial_cash = broker.initial_equity or args.initial_cash
        print(f"[bridge] signal : {signal_path}")
        print(f"[bridge] status : {status_path}")
    else:
        broker = MT5Broker(login=args.mt5_login, password=args.mt5_password,
                           server=args.mt5_server, terminal_path=args.mt5_path,
                           allow_real=args.allow_real).connect()
        # Real equity, not the 10,000 paper default - comparing a live account
        # against that invents however far apart the two happen to be.
        info = broker._mt5.account_info()
        if info is not None:
            args.initial_cash = float(info.equity)
            currency = info.currency

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"{args.symbol}_{args.timeframe}_{args.broker}_{args.feed}_{stamp}"
    log_path = os.path.join(args.out_dir, f"{tag}_trades.csv")
    fills_path = os.path.join(args.out_dir, f"{tag}_fills.csv")
    session_path = os.path.join(args.out_dir, f"{tag}_session.json")
    logger = TradeLogger(log_path)
    fills_log = FillLogger(fills_path)

    session = {
        "symbol": args.symbol, "timeframe": args.timeframe,
        "timeframe_minutes": timeframe_minutes(args.timeframe),
        "broker": args.broker, "feed": args.feed, "model": args.model,
        "volume": args.volume, "spread": args.spread,
        "contract_size": args.contract_size, "initial_cash": args.initial_cash,
        "currency": currency,
        "eval_horizon": args.eval_horizon, "threshold_pct": args.threshold_pct,
        "lookback": args.lookback, "pred_len": args.pred_len,
        "sample_count": args.sample_count, "seed": args.seed,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "trades_csv": os.path.basename(log_path), "fills_csv": os.path.basename(fills_path),
    }
    _write_session(session_path, session)   # written up front so a killed run is still reportable

    print(f"[cfg] broker={args.broker} feed={args.feed} symbol={args.symbol} {args.timeframe} "
          f"volume={args.volume} eval_h={args.eval_horizon} thr={args.threshold_pct}%")
    try:
        if args.feed == "replay":
            steps = run_replay(args, predictor, broker, logger, fills_log)
        else:
            steps = run_live(args, predictor, broker, logger, fills_log)
    finally:
        fills_log.sync(broker)
        logger.close()
        fills_log.close()

    equity = broker.account_equity()
    session["ended_at"] = datetime.now().isoformat(timespec="seconds")
    session["decisions"] = steps
    session["final_equity"] = equity
    print(f"\n[done] decisions={steps}  final equity={equity:.2f}")
    if getattr(broker, "fills", None) is not None:
        closes = [f for f in broker.fills if f.action == "close"]
        wins = sum(1 for f in closes if f.realized_pnl > 0)
        ret_pct = (equity - args.initial_cash) / args.initial_cash * 100 if args.initial_cash else float("nan")
        spread_paid = getattr(broker, "spread_paid", 0.0)
        session.update(closed_trades=len(closes), wins=wins,
                       net_pnl=equity - args.initial_cash, return_pct=ret_pct,
                       spread_paid=spread_paid)
        print(f"[done] closed trades={len(closes)}  wins={wins}  "
              f"hit_rate={(wins / len(closes) * 100) if closes else float('nan'):.1f}%  "
              f"return={ret_pct:+.2f}%" + (f"  spread paid={spread_paid:.2f}" if spread_paid else ""))
    _write_session(session_path, session)
    print(f"[done] trade log: {log_path}")
    print(f"[done] fills    : {fills_path}")

    if not args.no_html:
        try:
            from mt5_backtest.live_report import write_live_report

            html = write_live_report(session_path, os.path.join(args.out_dir, f"{tag}_report.html"))
            print(f"[done] HTML     : {html}")
            print(f"[done]            ^ open this one - equity curve, every trade, P&L explained")
        except Exception as exc:
            print(f"[done] HTML report skipped ({exc})")


if __name__ == "__main__":
    main()
