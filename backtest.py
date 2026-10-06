#!/usr/bin/env python3
"""Backtest the intraday crossover bot (Trading 212 setup: GBP account, US stocks).

Replays the live bot's rules bar by bar:
- 20/50 moving averages on 15-minute regular-session bars, signal when the relationship flips.
- Execution delay: the live bot runs 1 minute after each bar closes, so a signal from a bar
  fills at the NEXT bar's open (the 15:45 bar's signal fills at the next morning's open, as the
  16:01 run finds the market closed). --fill next_close models a further 15-minute delay.
- Fixed £ buys from a simulated cash account: no margin, sale proceeds usable next trading day,
  long-only, skip buy if already holding.
- £100 deposited on the first trading day of each month, on top of the starting cash.
- Dividends: credited on the ex-date for shares held, minus 15% US withholding (W-8BEN), and
  reinvested into the same stock.
- Costs: Trading 212's 0.15% FX fee on every GBP<->USD conversion, plus slippage on each fill.

Data: --source yahoo (last 60 days of 15m bars, no keys needed) or --source alpaca
(years of 15m bars; needs APCA_API_KEY_ID/APCA_API_SECRET_KEY in .env).

Usage:
  python backtest.py                                   # last 60 days via Yahoo
  python backtest.py --source alpaca --start 2021-01-01
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from trading_bot import BASE_DIR, MARKET_TZ, require_env

FX_FEE = 0.0015
WITHHOLDING_TAX = 0.15
BENCHMARK = "SPY"


# ---------- data ----------

def regular_session(df):
    times = df.index.time
    return df[(times >= time(9, 30)) & (times < time(16, 0))]


def load_bars_yahoo(symbols):
    bars = {}
    for symbol in symbols:
        df = yf.Ticker(symbol).history(period="60d", interval="15m", auto_adjust=False)
        if df.empty:
            raise ValueError(f"No Yahoo data for {symbol}")
        df.index = df.index.tz_convert(MARKET_TZ)
        bars[symbol] = regular_session(df[["Open", "Close"]].dropna())
    return bars


def load_bars_alpaca(symbols, start, end, feed):
    api_key, secret = require_env("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret}
    params = {"symbols": ",".join(symbols), "timeframe": "15Min", "start": start.isoformat(),
              "end": end.isoformat(), "feed": feed, "adjustment": "split", "limit": 10000}
    rows = defaultdict(list)
    while True:
        response = requests.get("https://data.alpaca.markets/v2/stocks/bars",
                                headers=headers, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
        for symbol, items in (data.get("bars") or {}).items():
            rows[symbol].extend(items)
        if not data.get("next_page_token"):
            break
        params["page_token"] = data["next_page_token"]
        print(f"  fetched {sum(len(v) for v in rows.values()):,} bars...", flush=True)
    bars = {}
    for symbol in symbols:
        if not rows[symbol]:
            raise ValueError(f"No Alpaca data for {symbol}")
        index = pd.DatetimeIndex([b["t"] for b in rows[symbol]]).tz_convert(MARKET_TZ)
        df = pd.DataFrame({"Open": [b["o"] for b in rows[symbol]], "Close": [b["c"] for b in rows[symbol]]},
                          index=index).sort_index()
        bars[symbol] = regular_session(df)
    return bars


def load_dividends(symbols):
    divs = {}
    for symbol in symbols:
        d = yf.Ticker(symbol).dividends
        divs[symbol] = {ts.date(): float(v) for ts, v in d.items()} if len(d) else {}
    return divs


def load_fx(start, end):
    fx = yf.Ticker("GBPUSD=X").history(start=(start - timedelta(days=10)).date().isoformat(),
                                       end=(end + timedelta(days=2)).date().isoformat())["Close"]
    fx.index = fx.index.date
    return fx


# ---------- simulation ----------

class Account:
    def __init__(self, starting_cash, fx_lookup, slippage):
        self.settled = float(starting_cash)
        self.unsettled = []  # (amount, trade_date)
        self.positions = {}  # symbol -> {"qty", "cost_gbp"}
        self.fx = fx_lookup
        self.slippage = slippage
        self.stats = defaultdict(float)
        self.closed_trades = []

    def settle(self, today):
        keep = []
        for amount, day in self.unsettled:
            if day < today:
                self.settled += amount
            else:
                keep.append((amount, day))
        self.unsettled = keep

    def buy(self, symbol, gbp, price, day):
        usd = gbp * self.fx(day) * (1 - FX_FEE)
        qty = usd / (price * (1 + self.slippage))
        self.settled -= gbp
        self.positions[symbol] = {"qty": qty, "cost_gbp": gbp}
        self.stats["fees_gbp"] += gbp * FX_FEE + gbp * (1 - FX_FEE) * self.slippage
        self.stats["buys"] += 1

    def sell(self, symbol, price, day):
        pos = self.positions.pop(symbol)
        usd = pos["qty"] * price * (1 - self.slippage)
        gbp = usd / self.fx(day) * (1 - FX_FEE)
        self.unsettled.append((gbp, day))
        self.stats["fees_gbp"] += pos["qty"] * price * self.slippage / self.fx(day) + usd / self.fx(day) * FX_FEE
        self.stats["sells"] += 1
        self.closed_trades.append(gbp - pos["cost_gbp"])

    def cash(self):
        return self.settled + sum(a for a, _ in self.unsettled)

    def value(self, last_price, day):
        held = sum(p["qty"] * last_price[s] / self.fx(day) for s, p in self.positions.items())
        return self.cash() + held


def simulate(bars, divs, fx_series, symbols, cfg):
    short_w, long_w = cfg["short_window"], cfg["long_window"]
    fill_col = "Open" if cfg["fill"] == "next_open" else "Close"

    def fx(day):
        return float(fx_series[fx_series.index <= day].iloc[-1])

    relations = {}
    for s in symbols:
        close = bars[s]["Close"]
        short_ma, long_ma = close.rolling(short_w).mean(), close.rolling(long_w).mean()
        rel = pd.Series(np.where(short_ma > long_ma, "above", "below"), index=close.index)
        relations[s] = rel[long_ma.notna()]

    timeline = sorted(set().union(*(bars[s].index for s in symbols + [BENCHMARK])))
    bar_at = {s: {t: (row.Open, row.Close) for t, row in bars[s].iterrows()} for s in symbols + [BENCHMARK]}

    acct = Account(cfg["starting_cash"], fx, cfg["slippage"])
    bench = {"qty": 0.0, "cash": float(cfg["starting_cash"]), "fees": 0.0}
    last_price = {}
    stored = {}
    pending = {}
    current_day, current_month = None, None
    deposited = float(cfg["starting_cash"])
    daily = []  # (day, strategy value, benchmark value, deposited)
    deposits_by_day = defaultdict(float)

    for t in timeline:
        day = t.date()
        if day != current_day:
            if current_day is not None:
                daily.append((current_day, acct.value(last_price, current_day),
                              bench["cash"] + bench["qty"] * last_price[BENCHMARK] / fx(current_day), deposited,
                              acct.cash()))
            current_day = day
            acct.settle(day)
            if current_month is not None and (day.year, day.month) != current_month:
                acct.settled += cfg["monthly_deposit"]
                bench["cash"] += cfg["monthly_deposit"]
                deposited += cfg["monthly_deposit"]
                deposits_by_day[day] += cfg["monthly_deposit"]
            current_month = (day.year, day.month)
            for s in symbols:
                if s in acct.positions and day in divs.get(s, {}) and s in last_price:
                    usd = acct.positions[s]["qty"] * divs[s][day] * (1 - WITHHOLDING_TAX)
                    acct.positions[s]["qty"] += usd / last_price[s]
                    acct.stats["dividends_gbp"] += usd / fx(day)
            if bench["qty"] and day in divs.get(BENCHMARK, {}) and BENCHMARK in last_price:
                usd = bench["qty"] * divs[BENCHMARK][day] * (1 - WITHHOLDING_TAX)
                bench["qty"] += usd / last_price[BENCHMARK]

        # Benchmark: invest all available cash at the first bar of the day.
        if t in bar_at[BENCHMARK] and bench["cash"] > 1:
            price = bar_at[BENCHMARK][t][0] * (1 + cfg["slippage"])
            bench["qty"] += bench["cash"] * fx(day) * (1 - FX_FEE) / price
            bench["fees"] += bench["cash"] * FX_FEE
            bench["cash"] = 0.0

        # Fill orders queued from the previous bar's signal, in watchlist (priority) order.
        for s in symbols:
            if s in pending and t in bar_at[s]:
                side = pending.pop(s)
                price = bar_at[s][t][0 if fill_col == "Open" else 1]
                if side == "buy":
                    if s in acct.positions:
                        acct.stats["buy_skipped_holding"] += 1
                    elif acct.settled < cfg["trade_amount"]:
                        acct.stats["buy_skipped_cash"] += 1
                    else:
                        acct.buy(s, cfg["trade_amount"], price, day)
                elif s in acct.positions:
                    acct.sell(s, price, day)

        # Evaluate signals on each completed bar.
        for s in symbols + [BENCHMARK]:
            if t in bar_at[s]:
                last_price[s] = bar_at[s][t][1]
        for s in symbols:
            if t not in relations[s].index:
                continue
            curr = relations[s][t]
            prev = stored.get(s)
            stored[s] = curr
            if prev == "below" and curr == "above":
                pending[s] = "buy"
            elif prev == "above" and curr == "below":
                pending[s] = "sell"

    daily.append((current_day, acct.value(last_price, current_day),
                  bench["cash"] + bench["qty"] * last_price[BENCHMARK] / fx(current_day), deposited, acct.cash()))
    columns = ["day", "strategy", "benchmark", "deposited", "strategy_cash"]
    return acct, bench, pd.DataFrame(daily, columns=columns).set_index("day"), deposits_by_day


def time_weighted(values, deposits_by_day):
    index = [1.0]
    for prev_day, day in zip(values.index[:-1], values.index[1:]):
        flow = deposits_by_day.get(day, 0.0)
        index.append(index[-1] * (values[day] - flow) / values[prev_day])
    return pd.Series(index, index=values.index)


def max_drawdown(twr):
    return float((twr / twr.cummax() - 1).min())


def report(acct, bench, daily, deposits_by_day, cfg):
    first, last = daily.index[0], daily.index[-1]
    final = daily.iloc[-1]
    twr_s, twr_b = time_weighted(daily["strategy"], deposits_by_day), time_weighted(daily["benchmark"], deposits_by_day)
    years = max((last - first).days / 365.25, 1e-9)
    wins = [p for p in acct.closed_trades if p > 0]
    held_value = final["strategy"] - acct.cash()

    def annualised(twr):
        return (twr.iloc[-1] ** (1 / years) - 1) if years >= 1 else None

    result = {
        "period": f"{first} to {last} ({(last - first).days} days, {len(daily)} trading days)",
        "fill": cfg["fill"],
        "deposited_gbp": round(final["deposited"], 2),
        "strategy_final_gbp": round(final["strategy"], 2),
        "strategy_profit_gbp": round(final["strategy"] - final["deposited"], 2),
        "strategy_time_weighted_return_pct": round((twr_s.iloc[-1] - 1) * 100, 2),
        "strategy_annualised_pct": None if annualised(twr_s) is None else round(annualised(twr_s) * 100, 2),
        "strategy_max_drawdown_pct": round(max_drawdown(twr_s) * 100, 2),
        "benchmark_final_gbp": round(final["benchmark"], 2),
        "benchmark_profit_gbp": round(final["benchmark"] - final["deposited"], 2),
        "benchmark_time_weighted_return_pct": round((twr_b.iloc[-1] - 1) * 100, 2),
        "benchmark_annualised_pct": None if annualised(twr_b) is None else round(annualised(twr_b) * 100, 2),
        "benchmark_max_drawdown_pct": round(max_drawdown(twr_b) * 100, 2),
        "buys": int(acct.stats["buys"]),
        "sells": int(acct.stats["sells"]),
        "buys_skipped_no_cash": int(acct.stats["buy_skipped_cash"]),
        "buys_skipped_already_holding": int(acct.stats["buy_skipped_holding"]),
        "closed_trades_win_rate_pct": round(100 * len(wins) / len(acct.closed_trades), 1) if acct.closed_trades else None,
        "closed_trades_total_pl_gbp": round(sum(acct.closed_trades), 2),
        "fees_fx_and_slippage_gbp": round(acct.stats["fees_gbp"], 2),
        "dividends_net_gbp": round(acct.stats["dividends_gbp"], 2),
        "open_positions": len(acct.positions),
        "open_positions_value_gbp": round(held_value, 2),
        "cash_idle_gbp": round(acct.cash(), 2),
        "average_cash_share_pct": round(100 * float((daily["strategy_cash"] / daily["strategy"]).mean()), 1),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config_trading212.json")
    parser.add_argument("--source", choices=("yahoo", "alpaca"), default="yahoo")
    parser.add_argument("--start", help="YYYY-MM-DD (alpaca source)")
    parser.add_argument("--end", help="YYYY-MM-DD (alpaca source, default today)")
    parser.add_argument("--feed", default="iex", help="alpaca feed: iex (what the live bot uses) or sip")
    parser.add_argument("--starting-cash", type=float, default=200)
    parser.add_argument("--monthly-deposit", type=float, default=100)
    parser.add_argument("--slippage", type=float, default=0.0005, help="per fill, e.g. 0.0005 = 0.05%%")
    parser.add_argument("--fill", choices=("next_open", "next_close"), default="next_open")
    args = parser.parse_args()

    config = json.loads((BASE_DIR / args.config).read_text())
    symbols = config["symbols"]
    cfg = {"short_window": config["short_window"], "long_window": config["long_window"],
           "trade_amount": config["trade_amount"], "starting_cash": args.starting_cash,
           "monthly_deposit": args.monthly_deposit, "slippage": args.slippage, "fill": args.fill}

    print(f"Loading 15-minute bars for {len(symbols)} symbols + {BENCHMARK} from {args.source}...", flush=True)
    if args.source == "yahoo":
        bars = load_bars_yahoo(symbols + [BENCHMARK])
    else:
        start = datetime.fromisoformat(args.start).replace(tzinfo=MARKET_TZ)
        end = datetime.fromisoformat(args.end).replace(tzinfo=MARKET_TZ) if args.end else datetime.now(MARKET_TZ) - timedelta(minutes=20)
        bars = load_bars_alpaca(symbols + [BENCHMARK], start, end, args.feed)
    start_ts = min(df.index[0] for df in bars.values())
    end_ts = max(df.index[-1] for df in bars.values())
    divs = load_dividends(symbols + [BENCHMARK])
    fx = load_fx(start_ts, end_ts)

    acct, bench, daily, deposits_by_day = simulate(bars, divs, fx, symbols, cfg)
    result = report(acct, bench, daily, deposits_by_day, cfg)
    print(json.dumps(result, indent=2))
    out = BASE_DIR / "backtest_results"
    out.mkdir(exist_ok=True)
    daily.to_csv(out / f"daily_{args.source}_{args.fill}.csv")
    (out / f"summary_{args.source}_{args.fill}.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
