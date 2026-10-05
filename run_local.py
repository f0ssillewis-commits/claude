#!/usr/bin/env python3
"""Run the trading bots on this machine every 15 minutes during US market hours.

Leave it running (and keep the computer awake): python run_local.py
Stop with Ctrl+C. Each bot runs as its own process, so one failing never stops the other.
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
MARKET_TZ = ZoneInfo("America/New_York")
INTERVAL_MINUTES = 15
# Wait a little after each 15-minute mark so the just-finished bar is available from the data feed.
DELAY_AFTER_BAR = timedelta(seconds=60)


def bot_configs():
    load_dotenv(BASE_DIR / ".env")
    configs = ["config.json"]
    if os.getenv("T212_API_KEY") and os.getenv("T212_API_SECRET"):
        configs.append("config_trading212.json")
    return configs


def next_run(now):
    minute = (now.minute // INTERVAL_MINUTES + 1) * INTERVAL_MINUTES
    mark = now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minute)
    return mark + DELAY_AFTER_BAR


def in_market_window(t):
    # The bots check Alpaca's market clock themselves (holidays, early closes);
    # this just avoids calling them overnight and at weekends.
    return t.weekday() < 5 and (9, 30) <= (t.hour, t.minute) <= (16, 0)


def main():
    print(f"Trading bots will run every {INTERVAL_MINUTES} minutes during US market hours "
          f"(9:30-16:00 ET). Press Ctrl+C to stop.", flush=True)
    while True:
        run_at = next_run(datetime.now(MARKET_TZ))
        while not in_market_window(run_at - DELAY_AFTER_BAR):
            run_at = next_run(run_at)
        local = run_at.astimezone()
        print(f"Next run: {run_at:%a %H:%M} ET ({local:%H:%M} local time)", flush=True)
        time.sleep(max(0, (run_at - datetime.now(MARKET_TZ)).total_seconds()))

        for config in bot_configs():
            print(f"--- {datetime.now(MARKET_TZ):%Y-%m-%d %H:%M} ET: {config} ---", flush=True)
            subprocess.run([sys.executable, str(BASE_DIR / "trading_bot.py"), str(BASE_DIR / config)],
                           cwd=BASE_DIR)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
