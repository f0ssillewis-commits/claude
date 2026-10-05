#!/usr/bin/env python3
"""Moving average crossover trading bot for an Alpaca paper trading account.

Trades dollar amounts against a simulated small cash account (no margin, sale
proceeds settle next trading day) so paper results reflect a low-capital live account.
"""

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "logs" / "trading_bot.log"
STATE_DIR = BASE_DIR / "state"
MARKET_TZ = ZoneInfo("America/New_York")
MODES = ("daily", "intraday")

log = logging.getLogger("trading_bot")


def setup_logging():
    LOG_PATH.parent.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S ET")
    formatter.converter = lambda t: datetime.fromtimestamp(t, MARKET_TZ).timetuple()
    for handler in (logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def load_config():
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    if config["mode"] not in MODES:
        sys.exit(f"ERROR: mode must be one of {MODES}")
    if config["short_window"] >= config["long_window"]:
        sys.exit("ERROR: short_window must be smaller than long_window")
    if config["mode"] == "daily" and config["lookback_days"] < config["long_window"] + 1:
        sys.exit("ERROR: lookback_days must be at least long_window + 1")
    return config


def load_credentials():
    load_dotenv(BASE_DIR / ".env")
    api_key = os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        sys.exit("ERROR: Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env or the environment")
    return {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}


# ---------- simulated cash account ----------

def load_state(config):
    path = STATE_DIR / f"{config['mode']}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"settled_cash": config["starting_cash_usd"], "unsettled": [], "last_relation": {}}


def save_state(config, state):
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / f"{config['mode']}.json").write_text(json.dumps(state, indent=2) + "\n")


def settle_cash(state, today):
    """Sale proceeds from an earlier trading day are settled (T+1) and spendable today."""
    pending = []
    for entry in state["unsettled"]:
        if entry["trade_date"] < today.isoformat():
            state["settled_cash"] = round(state["settled_cash"] + entry["amount"], 2)
        else:
            pending.append(entry)
    state["unsettled"] = pending


# ---------- market data ----------

def fetch_daily_closes(symbol, lookback_days, now):
    # Pull extra history so we're guaranteed `lookback_days` trading days after weekends/holidays.
    history = yf.Ticker(symbol).history(period="1y", interval="1d")
    if history.empty:
        raise ValueError(f"No price data returned for {symbol}")
    closes = history["Close"]
    # Before the 4pm close, today's bar is an in-progress price, not a real daily close.
    if closes.index[-1].date() == now.date() and now.hour < 16:
        closes = closes.iloc[:-1]
    return closes.tail(lookback_days)


def fetch_intraday_closes(config, headers, symbols, now):
    """Completed regular-session bars for every symbol, keyed by symbol."""
    minutes = config["bar_minutes"]
    params = {
        "symbols": ",".join(symbols),
        "timeframe": f"{minutes}Min",
        "start": (now - timedelta(days=10)).isoformat(),
        "feed": "iex",
        "limit": 10000,
    }
    rows = defaultdict(list)
    while True:
        response = requests.get(f"{config['alpaca_data_url']}/v2/stocks/bars",
                                headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        for symbol, bars in (data.get("bars") or {}).items():
            rows[symbol].extend(bars)
        if not data.get("next_page_token"):
            break
        params["page_token"] = data["next_page_token"]

    closes = {}
    for symbol in symbols:
        if not rows[symbol]:
            continue
        index = pd.DatetimeIndex([bar["t"] for bar in rows[symbol]]).tz_convert(MARKET_TZ)
        series = pd.Series([bar["c"] for bar in rows[symbol]], index=index).sort_index()
        times = series.index.time
        in_session = (times >= time(9, 30)) & (times < time(16, 0))
        completed = series.index + pd.Timedelta(minutes=minutes) <= now
        closes[symbol] = series[in_session & completed]
    return closes


# ---------- strategy ----------

def relation(short_ma, long_ma):
    return "above" if short_ma > long_ma else "below"


def detect_crossover(closes, short_window, long_window, prev_relation=None):
    """Return (signal, details). signal is "buy", "sell" or None.

    prev_relation is the short/long relationship at the last bar we evaluated; it makes
    the bot catch a crossover even if a scheduled run was skipped. Without it, the last
    two bars are compared.
    """
    if len(closes) < long_window + 1:
        raise ValueError(f"Only {len(closes)} bars available, need {long_window + 1}")
    short_ma = closes.rolling(short_window).mean()
    long_ma = closes.rolling(long_window).mean()
    curr = relation(short_ma.iloc[-1], long_ma.iloc[-1])
    if prev_relation is None:
        prev_relation = relation(short_ma.iloc[-2], long_ma.iloc[-2])

    details = {
        "bar": closes.index[-1],
        "close": closes.iloc[-1],
        "short_ma": short_ma.iloc[-1],
        "long_ma": long_ma.iloc[-1],
        "relation": curr,
    }
    if prev_relation == "below" and curr == "above":
        return "buy", details
    if prev_relation == "above" and curr == "below":
        return "sell", details
    return None, details


# ---------- Alpaca trading ----------

def is_market_open(base_url, headers):
    response = requests.get(f"{base_url}/v2/clock", headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()["is_open"]


def get_position(base_url, headers, symbol):
    response = requests.get(f"{base_url}/v2/positions/{symbol}", headers=headers, timeout=10)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def get_positions_value(base_url, headers, symbols):
    response = requests.get(f"{base_url}/v2/positions", headers=headers, timeout=10)
    response.raise_for_status()
    return sum(float(p["market_value"]) for p in response.json() if p["symbol"] in symbols)


def buy_notional(base_url, headers, symbol, amount):
    order = {
        "symbol": symbol,
        "notional": f"{amount:.2f}",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    response = requests.post(f"{base_url}/v2/orders", headers=headers, json=order, timeout=10)
    response.raise_for_status()
    return response.json()


def close_position(base_url, headers, symbol):
    response = requests.delete(f"{base_url}/v2/positions/{symbol}", headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


# ---------- run ----------

def process_symbol(symbol, closes, config, headers, state, today):
    base_url = config["alpaca_base_url"]
    amount = config["trade_amount_usd"]
    stored = state["last_relation"].get(symbol)

    signal, d = detect_crossover(closes, config["short_window"], config["long_window"],
                                 stored["relation"] if stored else None)
    bar_label = d["bar"].strftime("%Y-%m-%d %H:%M") if config["mode"] == "intraday" else d["bar"].date()
    log.info("[%s] %s close $%.2f | short MA $%.2f | long MA $%.2f | short %s long",
             symbol, bar_label, d["close"], d["short_ma"], d["long_ma"], d["relation"])

    result = "no signal"
    if signal == "buy":
        if get_position(base_url, headers, symbol):
            log.info("[%s] BUY signal, already holding. Skipped.", symbol)
            result = "buy skipped (holding)"
        elif state["settled_cash"] < amount:
            log.info("[%s] BUY signal, only $%.2f settled cash (need $%.2f). Skipped.",
                     symbol, state["settled_cash"], amount)
            result = "buy skipped (cash)"
        else:
            order = buy_notional(base_url, headers, symbol, amount)
            state["settled_cash"] = round(state["settled_cash"] - amount, 2)
            log.info("[%s] BUY $%.2f placed (order %s, %s)", symbol, amount, order["id"], order["status"])
            result = f"BUY ${amount:.2f}"
    elif signal == "sell":
        position = get_position(base_url, headers, symbol)
        if not position:
            log.info("[%s] SELL signal, not holding. Skipped.", symbol)
            result = "sell skipped (not holding)"
        else:
            proceeds = round(float(position["market_value"]), 2)
            order = close_position(base_url, headers, symbol)
            state["unsettled"].append({"amount": proceeds, "trade_date": today.isoformat()})
            log.info("[%s] SELL %s shares (~$%.2f, settles next trading day) placed (order %s, %s)",
                     symbol, position["qty"], proceeds, order["id"], order["status"])
            result = f"SELL ~${proceeds:.2f}"

    # Only record the new relationship once the signal has been fully handled.
    state["last_relation"][symbol] = {"relation": d["relation"], "bar": str(d["bar"])}
    return result


def main():
    config = load_config()
    headers = load_credentials()
    base_url = config["alpaca_base_url"]
    symbols = config["symbols"]

    if not is_market_open(base_url, headers):
        print("Market is closed. No action taken.")
        return True

    setup_logging()
    now = datetime.now(MARKET_TZ)
    state = load_state(config)
    settle_cash(state, now.date())
    log.info("=== %s run: %d symbols | settled cash $%.2f ===",
             config["mode"], len(symbols), state["settled_cash"])

    intraday_closes = fetch_intraday_closes(config, headers, symbols, now) if config["mode"] == "intraday" else None

    results = {}
    for symbol in symbols:
        try:
            if intraday_closes is not None:
                if symbol not in intraday_closes:
                    raise ValueError(f"No bars returned for {symbol}")
                closes = intraday_closes[symbol]
            else:
                closes = fetch_daily_closes(symbol, config["lookback_days"], now)
            results[symbol] = process_symbol(symbol, closes, config, headers, state, now.date())
        except requests.exceptions.HTTPError as e:
            log.error("[%s] Alpaca API returned %s: %s", symbol, e.response.status_code, e.response.text)
            results[symbol] = "ERROR"
        except Exception:
            log.exception("[%s] Unexpected error", symbol)
            results[symbol] = "ERROR"

    save_state(config, state)
    unsettled = sum(e["amount"] for e in state["unsettled"])
    positions = get_positions_value(base_url, headers, symbols)
    actions = {s: r for s, r in results.items() if r != "no signal"}
    log.info("=== Summary: %s ===", " | ".join(f"{s}: {r}" for s, r in actions.items()) or "no signals")
    log.info("=== Account: settled $%.2f + unsettled $%.2f + positions $%.2f = $%.2f (started $%.2f) ===",
             state["settled_cash"], unsettled, positions,
             state["settled_cash"] + unsettled + positions, config["starting_cash_usd"])
    return "ERROR" not in results.values()


if __name__ == "__main__":
    try:
        sys.exit(0 if main() else 1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: Alpaca API returned {e.response.status_code}: {e.response.text}")
        sys.exit(1)
