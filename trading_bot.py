#!/usr/bin/env python3
"""Moving average crossover trading bot for Alpaca (paper) or Trading 212 (practice).

Trades fixed cash amounts against a simulated small cash account (no margin, sale
proceeds settle next trading day) so practice results reflect a low-capital live account.

Usage: python trading_bot.py [config file]   (default: config.json)
"""

import base64
import json
import logging
import math
import os
import sys
import time as time_mod
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
MARKET_TZ = ZoneInfo("America/New_York")
MODES = ("daily", "intraday")
BROKERS = ("alpaca", "trading212")

log = logging.getLogger("trading_bot")


def setup_logging(log_path):
    log_path.parent.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S ET")
    formatter.converter = lambda t: datetime.fromtimestamp(t, MARKET_TZ).timetuple()
    for handler in (logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def load_config(path):
    with open(path) as f:
        config = json.load(f)
    config.setdefault("broker", "alpaca")
    config.setdefault("currency_symbol", "$")
    config.setdefault("log_file", "logs/trading_bot.log")
    config.setdefault("state_file", f"state/{config['mode']}.json")
    if config["broker"] not in BROKERS:
        sys.exit(f"ERROR: broker must be one of {BROKERS}")
    if config["mode"] not in MODES:
        sys.exit(f"ERROR: mode must be one of {MODES}")
    if config["short_window"] >= config["long_window"]:
        sys.exit("ERROR: short_window must be smaller than long_window")
    if config["mode"] == "daily" and config["lookback_days"] < config["long_window"] + 1:
        sys.exit("ERROR: lookback_days must be at least long_window + 1")
    return config


def require_env(*names):
    load_dotenv(BASE_DIR / ".env")
    values = [os.getenv(name) for name in names]
    if not all(values):
        sys.exit(f"ERROR: Set {' and '.join(names)} in .env or the environment")
    return values


def load_credentials():
    """Alpaca credentials: used for trading on Alpaca, and for market data/clock with either broker."""
    api_key, secret_key = require_env("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
    return {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}


# ---------- simulated cash account ----------

def load_state(config):
    path = BASE_DIR / config["state_file"]
    if path.exists():
        return json.loads(path.read_text())
    return {"settled_cash": config["starting_cash"], "unsettled": [], "last_relation": {}}


def save_state(config, state):
    path = BASE_DIR / config["state_file"]
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def settle_cash(state, today):
    """Sale proceeds from an earlier trading day are settled (T+1) and spendable today."""
    pending = []
    for entry in state["unsettled"]:
        if entry["trade_date"] < today.isoformat():
            state["settled_cash"] = round(state["settled_cash"] + entry["amount"], 2)
        else:
            pending.append(entry)
    state["unsettled"] = pending


# ---------- market data (Alpaca data API / yfinance; prices in USD) ----------

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


def is_market_open(config, headers):
    response = requests.get(f"{config['alpaca_base_url']}/v2/clock", headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()["is_open"]


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


# ---------- brokers ----------
# positions() -> {symbol: {"qty": float, "value": float in account currency}}
# buy(symbol, amount, price_usd) -> (order, amount actually spent)
# sell(symbol, position) -> order

class AlpacaBroker:
    name = "Alpaca"

    def __init__(self, config, headers):
        self.base = config["alpaca_base_url"]
        self.headers = headers

    def positions(self, symbols):
        response = requests.get(f"{self.base}/v2/positions", headers=self.headers, timeout=10)
        response.raise_for_status()
        return {p["symbol"]: {"qty": float(p["qty"]), "value": float(p["market_value"])}
                for p in response.json() if p["symbol"] in symbols}

    def buy(self, symbol, amount, price_usd):
        order = {"symbol": symbol, "notional": f"{amount:.2f}", "side": "buy",
                 "type": "market", "time_in_force": "day"}
        response = requests.post(f"{self.base}/v2/orders", headers=self.headers, json=order, timeout=10)
        response.raise_for_status()
        return response.json(), amount

    def sell(self, symbol, position):
        response = requests.delete(f"{self.base}/v2/positions/{symbol}", headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()


class Trading212Broker:
    """Trading 212 Invest/ISA API. Orders are by share quantity in the GBP account,
    so cash amounts are converted to fractional quantities using the latest USD price
    and the GBP/USD rate."""

    name = "Trading 212"
    QUANTITY_DECIMALS = (4, 3, 2, 1)

    def __init__(self, config, symbols):
        api_key, api_secret = require_env("T212_API_KEY", "T212_API_SECRET")
        token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}"}
        self.base = config["t212_base_url"]
        self._last_call = 0.0
        self._usd_per_gbp = None
        self.tickers = self._resolve_tickers(symbols)
        self.symbol_for = {ticker: symbol for symbol, ticker in self.tickers.items()}
        self._log_account()

    def _request(self, method, path, **kwargs):
        # Trading 212 rate limits are per endpoint and as low as 1 request/second.
        wait = 1.1 - (time_mod.monotonic() - self._last_call)
        if wait > 0:
            time_mod.sleep(wait)
        try:
            response = requests.request(method, f"{self.base}{path}", headers=self.headers, timeout=15, **kwargs)
        finally:
            self._last_call = time_mod.monotonic()
        response.raise_for_status()
        return response.json()

    def _resolve_tickers(self, symbols):
        instruments = self._request("GET", "/equity/metadata/instruments")
        by_ticker = {i["ticker"]: i for i in instruments}
        tickers = {}
        for symbol in symbols:
            if f"{symbol}_US_EQ" in by_ticker:
                tickers[symbol] = f"{symbol}_US_EQ"
                continue
            match = next((i["ticker"] for i in instruments
                          if i.get("shortName") == symbol and i.get("currencyCode") == "USD"), None)
            if match:
                tickers[symbol] = match
            else:
                log.warning("[%s] not available on Trading 212; it will be skipped", symbol)
        return tickers

    def _log_account(self):
        try:
            summary = self._request("GET", "/equity/account/summary")
            currency = summary.get("currency")
            log.info("Trading 212 account %s: currency %s, available to trade %s",
                     summary.get("id"), currency, summary.get("cash", {}).get("availableToTrade"))
            if currency and currency != "GBP":
                log.warning("Account currency is %s, but trade sizes assume GBP", currency)
        except requests.exceptions.RequestException as e:
            log.warning("Could not read Trading 212 account summary: %s", e)

    def usd_per_gbp(self):
        if self._usd_per_gbp is None:
            rates = yf.Ticker("GBPUSD=X").history(period="5d")["Close"]
            if rates.empty:
                raise ValueError("Could not fetch GBP/USD exchange rate")
            self._usd_per_gbp = float(rates.iloc[-1])
        return self._usd_per_gbp

    def ticker(self, symbol):
        if symbol not in self.tickers:
            raise ValueError(f"{symbol} is not available on Trading 212")
        return self.tickers[symbol]

    def positions(self, symbols):
        result = {}
        for p in self._request("GET", "/equity/positions"):
            ticker = p.get("ticker") or (p.get("instrument") or {}).get("ticker")
            symbol = self.symbol_for.get(ticker)
            if symbol not in symbols:
                continue
            qty = float(p.get("quantityAvailableForTrading", p["quantity"]))
            value = (p.get("walletImpact") or {}).get("currentValue")
            if value is None:
                value = float(p["quantity"]) * float(p["currentPrice"]) / self.usd_per_gbp()
            result[symbol] = {"qty": qty, "value": float(value)}
        return result

    def buy(self, symbol, amount, price_usd):
        ticker = self.ticker(symbol)
        raw_qty = amount * self.usd_per_gbp() / price_usd
        last_error = None
        # Instruments allow different quantity precisions; a 400 means the order was
        # rejected (not placed), so it is safe to retry with fewer decimals.
        for decimals in self.QUANTITY_DECIMALS:
            qty = math.floor(raw_qty * 10 ** decimals) / 10 ** decimals
            if qty <= 0:
                break
            try:
                order = self._request("POST", "/equity/orders/market",
                                      json={"ticker": ticker, "quantity": qty, "extendedHours": False})
            except requests.exceptions.HTTPError as e:
                if e.response.status_code != 400:
                    raise
                last_error = e
                log.info("[%s] quantity %s rejected: %s", symbol, qty, e.response.text)
                continue
            spent = round(qty * price_usd / self.usd_per_gbp(), 2)
            return order, spent
        if last_error:
            raise last_error
        raise ValueError(f"{amount} is too small to buy any {symbol}")

    def sell(self, symbol, position):
        return self._request("POST", "/equity/orders/market",
                             json={"ticker": self.ticker(symbol), "quantity": -position["qty"],
                                   "extendedHours": False})


def make_broker(config, alpaca_headers, symbols):
    if config["broker"] == "trading212":
        return Trading212Broker(config, symbols)
    return AlpacaBroker(config, alpaca_headers)


# ---------- run ----------

def process_symbol(symbol, closes, config, broker, positions, state, today):
    cur = config["currency_symbol"]
    amount = config["trade_amount"]
    stored = state["last_relation"].get(symbol)

    signal, d = detect_crossover(closes, config["short_window"], config["long_window"],
                                 stored["relation"] if stored else None)
    bar_label = d["bar"].strftime("%Y-%m-%d %H:%M") if config["mode"] == "intraday" else d["bar"].date()
    log.info("[%s] %s close $%.2f | short MA $%.2f | long MA $%.2f | short %s long",
             symbol, bar_label, d["close"], d["short_ma"], d["long_ma"], d["relation"])

    result = "no signal"
    if signal == "buy":
        if symbol in positions:
            log.info("[%s] BUY signal, already holding. Skipped.", symbol)
            result = "buy skipped (holding)"
        elif state["settled_cash"] < amount:
            log.info("[%s] BUY signal, only %s%.2f settled cash (need %s%.2f). Skipped.",
                     symbol, cur, state["settled_cash"], cur, amount)
            result = "buy skipped (cash)"
        else:
            order, spent = broker.buy(symbol, amount, d["close"])
            state["settled_cash"] = round(state["settled_cash"] - spent, 2)
            positions[symbol] = {"qty": 0.0, "value": spent}
            log.info("[%s] BUY %s%.2f placed (order %s, %s)", symbol, cur, spent, order.get("id"), order.get("status"))
            result = f"BUY {cur}{spent:.2f}"
    elif signal == "sell":
        position = positions.get(symbol)
        if not position:
            log.info("[%s] SELL signal, not holding. Skipped.", symbol)
            result = "sell skipped (not holding)"
        else:
            proceeds = round(position["value"], 2)
            order = broker.sell(symbol, position)
            state["unsettled"].append({"amount": proceeds, "trade_date": today.isoformat()})
            positions.pop(symbol)
            log.info("[%s] SELL %g shares (~%s%.2f, settles next trading day) placed (order %s, %s)",
                     symbol, position["qty"], cur, proceeds, order.get("id"), order.get("status"))
            result = f"SELL ~{cur}{proceeds:.2f}"

    # Only record the new relationship once the signal has been fully handled.
    state["last_relation"][symbol] = {"relation": d["relation"], "bar": str(d["bar"])}
    return result


def main(config_path=BASE_DIR / "config.json"):
    config = load_config(config_path)
    alpaca_headers = load_credentials()
    symbols = config["symbols"]
    cur = config["currency_symbol"]

    if not is_market_open(config, alpaca_headers):
        print("Market is closed. No action taken.")
        return True

    setup_logging(BASE_DIR / config["log_file"])
    now = datetime.now(MARKET_TZ)
    state = load_state(config)
    settle_cash(state, now.date())

    try:
        broker = make_broker(config, alpaca_headers, symbols)
        positions = broker.positions(symbols)
    except requests.exceptions.HTTPError as e:
        log.error("Broker API returned %s: %s", e.response.status_code, e.response.text)
        return False
    log.info("=== %s %s run: %d symbols | settled cash %s%.2f ===",
             broker.name, config["mode"], len(symbols), cur, state["settled_cash"])

    intraday_closes = (fetch_intraday_closes(config, alpaca_headers, symbols, now)
                       if config["mode"] == "intraday" else None)

    results = {}
    for symbol in symbols:
        try:
            if intraday_closes is not None:
                if symbol not in intraday_closes:
                    raise ValueError(f"No bars returned for {symbol}")
                closes = intraday_closes[symbol]
            else:
                closes = fetch_daily_closes(symbol, config["lookback_days"], now)
            results[symbol] = process_symbol(symbol, closes, config, broker, positions, state, now.date())
        except requests.exceptions.HTTPError as e:
            log.error("[%s] API returned %s: %s", symbol, e.response.status_code, e.response.text)
            results[symbol] = "ERROR"
        except Exception:
            log.exception("[%s] Unexpected error", symbol)
            results[symbol] = "ERROR"

    save_state(config, state)
    unsettled = sum(e["amount"] for e in state["unsettled"])
    positions_value = sum(p["value"] for p in broker.positions(symbols).values())
    actions = {s: r for s, r in results.items() if r != "no signal"}
    log.info("=== Summary: %s ===", " | ".join(f"{s}: {r}" for s, r in actions.items()) or "no signals")
    log.info("=== Account: settled %s%.2f + unsettled %s%.2f + positions %s%.2f = %s%.2f (started %s%.2f) ===",
             cur, state["settled_cash"], cur, unsettled, cur, positions_value,
             cur, state["settled_cash"] + unsettled + positions_value, cur, config["starting_cash"])
    return "ERROR" not in results.values()


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR / "config.json"
    try:
        sys.exit(0 if main(path) else 1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: API returned {e.response.status_code}: {e.response.text}")
        sys.exit(1)
