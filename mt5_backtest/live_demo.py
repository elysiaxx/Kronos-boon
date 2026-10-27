"""
Forward-test / demo trading loop for Kronos on MetaTrader 5.

Each time a new bar closes, feed the last ``lookback`` bars to Kronos, turn
the forecast into a long/short/flat signal, and act on it through a broker:

* ``--broker paper`` (default)  simulate fills in memory, place nothing.
* ``--broker mt5``              send market orders to a running MT5 terminal
                                (Windows only, DEMO account by default).

Bars can come from the live MT5 feed (``--feed mt5``) or be replayed from a
CSV (``--feed replay --csv file.csv``). Replay + paper is a fully offline
dry-run of the exact same loop, and is how the logic is validated without a
terminal.

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

Paper-trade the live market (real data, simulated orders):
    python -m mt5_backtest.live_demo --broker paper --feed mt5 \
        --symbol XAUUSD --timeframe M15 --max_bars 96

Trade a DEMO account for real:
    python -m mt5_backtest.live_demo --broker mt5 --feed mt5 \
        --symbol XAUUSD --timeframe M15 --volume 0.01 \
        --mt5_login 123 --mt5_password pw --mt5_server Broker-Demo --max_bars 96
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime

import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mt5_backtest.data_source import CSVDataSource, MT5DataSource, timeframe_minutes  # noqa: E402
from mt5_backtest.strategy import SignalGenerator  # noqa: E402
from mt5_backtest.trader import PaperBroker, MT5Broker  # noqa: E402
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


def run_replay(args, predictor, broker, logger):
    src = CSVDataSource(args.csv)
    df = src.fetch(symbol=args.symbol, timeframe=args.timeframe)
    n = len(df)
    if n < args.lookback + 2:
        raise SystemExit(f"CSV has {n} bars; need at least lookback+2 = {args.lookback + 2}.")

    print(f"[replay] {n} bars, iterating from bar {args.lookback} onward")
    steps = 0
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

        steps += 1
        if steps % max(1, (n // 20)) == 0 or action not in ("hold", "flat"):
            print(f"[replay] {next_bar['timestamps']}  dir={sig.direction:+d} "
                  f"move={sig.expected_move_pct:+.3f}%  {action:12s} equity={equity:.2f}")
        if args.max_bars and steps >= args.max_bars:
            break

    # flatten at the end so realized PnL is final
    last_close = float(df["close"].iloc[-1])
    broker.close_position(args.symbol, last_close, time=df["timestamps"].iloc[-1])
    return steps


def _latest_closed_bars(source, symbol, timeframe, count):
    """Fetch bars and drop the last one (still forming)."""
    df = source.fetch(symbol=symbol, timeframe=timeframe, count=count + 1)
    return df.iloc[:-1].reset_index(drop=True)


def run_live(args, predictor, broker, logger):
    source = MT5DataSource(login=args.mt5_login, password=args.mt5_password,
                           server=args.mt5_server, terminal_path=args.mt5_path)
    source.connect()
    period = timeframe_minutes(args.timeframe)
    print(f"[live] feed=MT5 {args.symbol} {args.timeframe} (bar = {period} min). Ctrl+C to stop.")

    last_bar_time = None
    steps = 0
    started = time.time()
    try:
        while True:
            bars = _latest_closed_bars(source, args.symbol, args.timeframe, args.lookback)
            cur_bar_time = bars["timestamps"].iloc[-1]
            if cur_bar_time != last_bar_time:
                last_bar_time = cur_bar_time
                hist = bars.iloc[-args.lookback:]
                x_ts = hist["timestamps"]
                stepd = pd.Timedelta(minutes=period)
                y_ts = pd.Series([cur_bar_time + stepd * (i + 1) for i in range(args.pred_len)])
                sig = siggen.generate(hist, x_ts, y_ts)
                action, fill_price = _act_on_signal(broker, args.symbol, sig, None, cur_bar_time)
                equity = broker.account_equity()
                logger.log(cur_bar_time, sig, action, fill_price, equity)
                print(f"[live] {cur_bar_time}  dir={sig.direction:+d} "
                      f"move={sig.expected_move_pct:+.3f}%  {action:12s} equity={equity:.2f}")
                steps += 1
                if args.max_bars and steps >= args.max_bars:
                    print("[live] max_bars reached; stopping.")
                    break
                if args.duration_min and (time.time() - started) / 60 >= args.duration_min:
                    print("[live] duration reached; stopping.")
                    break
            time.sleep(args.poll_sec)
    except KeyboardInterrupt:
        print("\n[live] interrupted by user.")
    finally:
        if args.flatten_on_exit:
            broker.close_position(args.symbol, None, time=datetime.now())
            print("[live] position flattened on exit.")
        source.shutdown()
    return steps


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
    p.add_argument("--broker", choices=["paper", "mt5"], default="paper")
    p.add_argument("--feed", choices=["mt5", "replay"], default="mt5")
    p.add_argument("--csv", default=None, help="CSV for --feed replay")
    p.add_argument("--volume", type=float, default=0.01, help="lot size per trade")
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
    return p


def main(argv=None):
    global siggen
    args = build_arg_parser().parse_args(argv)

    if args.broker == "mt5" and args.feed != "mt5":
        raise SystemExit("--broker mt5 requires --feed mt5 (cannot send real orders on replayed data).")
    if args.feed == "replay" and not args.csv:
        raise SystemExit("--feed replay requires --csv <path>.")

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

    if args.broker == "paper":
        broker = PaperBroker(initial_cash=args.initial_cash, contract_size=args.contract_size)
    else:
        broker = MT5Broker(login=args.mt5_login, password=args.mt5_password,
                           server=args.mt5_server, terminal_path=args.mt5_path,
                           allow_real=args.allow_real).connect()

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(args.out_dir, f"{args.symbol}_{args.timeframe}_{args.broker}_{args.feed}_{stamp}_trades.csv")
    logger = TradeLogger(log_path)

    print(f"[cfg] broker={args.broker} feed={args.feed} symbol={args.symbol} {args.timeframe} "
          f"volume={args.volume} eval_h={args.eval_horizon} thr={args.threshold_pct}%")
    try:
        if args.feed == "replay":
            steps = run_replay(args, predictor, broker, logger)
        else:
            steps = run_live(args, predictor, broker, logger)
    finally:
        logger.close()

    equity = broker.account_equity()
    print(f"\n[done] decisions={steps}  final equity={equity:.2f}")
    if isinstance(broker, PaperBroker):
        n_closed = sum(1 for f in broker.fills if f.action == "close")
        wins = sum(1 for f in broker.fills if f.action == "close" and f.realized_pnl > 0)
        ret_pct = (equity - args.initial_cash) / args.initial_cash * 100
        print(f"[done] closed trades={n_closed}  wins={wins}  "
              f"hit_rate={(wins / n_closed * 100) if n_closed else float('nan'):.1f}%  "
              f"return={ret_pct:+.2f}%")
    print(f"[done] trade log: {log_path}")


if __name__ == "__main__":
    main()
