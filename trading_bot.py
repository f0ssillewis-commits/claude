#!/usr/bin/env python3
"""Moving average crossover trading bot for an Alpaca paper trading account."""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "logs" / "trading_bot.log"
MARKET_TZ = ZoneInfo("America/New_York")

log = logging.getLogger("trading_bot")


def setup_logging():
    LOG_PATH.parent.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S ET")
    formatter.converter = lambda t: datetime.fromtimestamp(t, MARKET_TZ).timetuple()
    for handler in (logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def fail(message):
    log.error(message)
    sys.exit(1)


def load_config():
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    if config["short_window"] >= config["long_window"]:
        fail("short_window must be smaller than long_window")
    if config["lookback_days"] < config["long_window"] + 1:
        fail("lookback_days must be at least long_window + 1")
    return config


def load_credentials():
    load_dotenv(BASE_DIR / ".env")
    api_key = os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        fail("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env or the environment")
    return {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}


def is_trading_day(base_url, headers, today):
    params = {"start": today.isoformat(), "end": today.isoformat()}
    response = requests.get(f"{base_url}/v2/calendar", headers=headers, params=params, timeout=10)
    response.raise_for_status()
    return any(day["date"] == today.isoformat() for day in response.json())


def fetch_closes(symbol, lookback_days, now):
    # Pull extra history so we're guaranteed `lookback_days` trading days after weekends/holidays.
    history = yf.Ticker(symbol).history(period="1y", interval="1d")
    if history.empty:
        fail(f"No price data returned for {symbol}")
    closes = history["Close"]
    # Before the 4pm close, today's bar is an in-progress price, not a real daily close.
    if closes.index[-1].date() == now.date() and now.hour < 16:
        closes = closes.iloc[:-1]
    return closes.tail(lookback_days)


def detect_crossover(closes, short_window, long_window):
    """Return ("buy" | "sell" | None, details) based on the last two trading days."""
    short_ma = closes.rolling(short_window).mean()
    long_ma = closes.rolling(long_window).mean()

    prev_short, curr_short = short_ma.iloc[-2], short_ma.iloc[-1]
    prev_long, curr_long = long_ma.iloc[-2], long_ma.iloc[-1]

    details = {
        "date": closes.index[-1].date(),
        "close": closes.iloc[-1],
        "prev_short": prev_short,
        "prev_long": prev_long,
        "curr_short": curr_short,
        "curr_long": curr_long,
    }

    if prev_short <= prev_long and curr_short > curr_long:
        return "buy", details
    if prev_short >= prev_long and curr_short < curr_long:
        return "sell", details
    return None, details


def get_position_qty(base_url, headers, symbol):
    response = requests.get(f"{base_url}/v2/positions/{symbol}", headers=headers, timeout=10)
    if response.status_code == 404:
        return 0.0
    response.raise_for_status()
    return float(response.json()["qty"])


def place_market_order(base_url, headers, symbol, qty, side):
    order = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    response = requests.post(f"{base_url}/v2/orders", headers=headers, json=order, timeout=10)
    response.raise_for_status()
    return response.json()


def main():
    config = load_config()
    headers = load_credentials()
    symbol = config["symbol"]
    qty = config["trade_quantity"]
    base_url = config["alpaca_base_url"]
    now = datetime.now(MARKET_TZ)

    log.info("=== Run started for %s ===", symbol)

    if not is_trading_day(base_url, headers, now.date()):
        log.info("Market is closed today (%s). No action taken.", now.date())
        return

    log.info("Fetching last %d trading days of %s", config["lookback_days"], symbol)
    closes = fetch_closes(symbol, config["lookback_days"], now)

    signal, d = detect_crossover(closes, config["short_window"], config["long_window"])
    s, l = config["short_window"], config["long_window"]
    log.info("Latest close %s: $%.2f", d["date"], d["close"])
    log.info("Previous day: %d-day MA $%.2f | %d-day MA $%.2f", s, d["prev_short"], l, d["prev_long"])
    log.info("Latest day:   %d-day MA $%.2f | %d-day MA $%.2f", s, d["curr_short"], l, d["curr_long"])

    if signal is None:
        log.info("No crossover detected. No trade placed.")
        return

    log.info("Crossover detected: %s signal", signal.upper())
    position_qty = get_position_qty(base_url, headers, symbol)
    log.info("Current %s position: %g share(s)", symbol, position_qty)

    # Long-only: never open a short, and don't stack buys on an existing position.
    if signal == "buy" and position_qty > 0:
        log.info("Already holding %s. Skipping buy.", symbol)
        return
    if signal == "sell" and position_qty < qty:
        log.info("Not enough shares to sell %d. Skipping sell.", qty)
        return

    order = place_market_order(base_url, headers, symbol, qty, signal)
    log.info("Order placed: %s %d %s (order id %s, status %s)",
             signal.upper(), qty, symbol, order["id"], order["status"])


if __name__ == "__main__":
    setup_logging()
    try:
        main()
    except requests.exceptions.HTTPError as e:
        fail(f"Alpaca API returned {e.response.status_code}: {e.response.text}")
    except Exception:
        log.exception("Unexpected error")
        sys.exit(1)
