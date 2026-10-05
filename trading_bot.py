#!/usr/bin/env python3
"""Moving average crossover trading bot for an Alpaca paper trading account."""

import json
import os
import sys
from pathlib import Path

import requests
import yfinance as yf
from dotenv import load_dotenv

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    if config["short_window"] >= config["long_window"]:
        sys.exit("ERROR: short_window must be smaller than long_window")
    if config["lookback_days"] < config["long_window"] + 1:
        sys.exit("ERROR: lookback_days must be at least long_window + 1")
    return config


def load_credentials():
    load_dotenv(Path(__file__).parent / ".env")
    api_key = os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        sys.exit("ERROR: Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env (see .env.example)")
    return {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}


def fetch_closes(symbol, lookback_days):
    # Pull extra history so we're guaranteed `lookback_days` trading days after weekends/holidays.
    history = yf.Ticker(symbol).history(period="1y", interval="1d")
    if history.empty:
        sys.exit(f"ERROR: No price data returned for {symbol}")
    return history["Close"].tail(lookback_days)


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

    print(f"Fetching last {config['lookback_days']} trading days of {symbol}...")
    closes = fetch_closes(symbol, config["lookback_days"])

    signal, d = detect_crossover(closes, config["short_window"], config["long_window"])
    s, l = config["short_window"], config["long_window"]
    print(f"As of {d['date']} (close ${d['close']:.2f}):")
    print(f"  Previous day: {s}-day MA ${d['prev_short']:.2f} | {l}-day MA ${d['prev_long']:.2f}")
    print(f"  Latest day:   {s}-day MA ${d['curr_short']:.2f} | {l}-day MA ${d['curr_long']:.2f}")

    if signal is None:
        print("No crossover detected. No trade placed.")
        return

    print(f"Crossover detected: {signal.upper()} signal")
    position_qty = get_position_qty(base_url, headers, symbol)

    # Long-only: never open a short, and don't stack buys on an existing position.
    if signal == "buy" and position_qty > 0:
        print(f"Already holding {position_qty:g} share(s) of {symbol}. Skipping buy.")
        return
    if signal == "sell" and position_qty < qty:
        print(f"Holding {position_qty:g} share(s) of {symbol}, need {qty} to sell. Skipping sell.")
        return

    order = place_market_order(base_url, headers, symbol, qty, signal)
    print(f"Order placed: {signal.upper()} {qty} {symbol} (order id {order['id']}, status {order['status']})")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.HTTPError as e:
        sys.exit(f"ERROR: Alpaca API returned {e.response.status_code}: {e.response.text}")
