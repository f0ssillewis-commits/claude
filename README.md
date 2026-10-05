# Moving Average Crossover Bot (Alpaca + Trading 212)

Practice-trades a list of stocks/ETFs as a rehearsal for a **small live account (~£200 / ~$260)**. The same strategy runs side by side on two brokers, each with its own config, budget, state and log:

| Broker | Config | Account | Budget | Log | State |
|---|---|---|---|---|---|
| Alpaca (US) | `config.json` | Paper | $260, $20 per buy | `logs/trading_bot.log` | `state/intraday.json` |
| Trading 212 (UK) | `config_trading212.json` | Practice (demo) | £200, £15 per buy | `logs/trading212.log` | `state/trading212_intraday.json` |

Prices and the market-open check come from Alpaca's market data for both brokers (Trading 212's API has no price data). Each symbol gets the same moving average crossover:

- **Buy** a fixed dollar amount when the short MA crosses *above* the long MA
- **Sell** the whole position when the short MA crosses *below* the long MA

The bot is long-only: it skips a buy if it already holds the symbol and skips a sell if it holds nothing (it never opens a short).

## Simulated small cash account

The practice accounts hold far more money than a real £200 account, so each bot keeps its own budget in its state file and only trades within it:

- Starts with `starting_cash` and buys `trade_amount` per signal using fractional shares.
- No margin: a buy is skipped if there isn't enough **settled** cash.
- Sale proceeds settle the next trading day (T+1), like a real cash account, and can't be reused until then.

Delete a bot's state file to reset its budget.

## Modes

| Mode | Bars | Data source | When signals can fire |
|---|---|---|---|
| `intraday` (default) | 15-minute | Alpaca market data (IEX feed) | Any 15-minute bar during market hours |
| `daily` | Daily closes | yfinance | Once per day, at the first run after a crossover |

Only completed bars are used: the bar still forming is ignored. Extended-hours bars are excluded in intraday mode. The bot remembers each symbol's last above/below state, so a crossover that happens between two runs (e.g. a delayed or skipped scheduled run) is still caught.

Run one mode at a time per paper account: both modes trade the same Alpaca positions.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys
```

`.env` needs:

- `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`: Alpaca paper keys (needed by both bots for market data and the market clock)
- `T212_API_KEY`, `T212_API_SECRET`: Trading 212 practice keys (the Trading 212 bot is skipped until these are set)

`.env` is git-ignored, so your credentials stay on your machine.

## Configuration (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `mode` | `intraday` | `intraday` or `daily` |
| `symbols` | 25 symbols (see below) | Stocks/ETFs to monitor and trade |
| `short_window` | `20` | Short moving average period (bars) |
| `long_window` | `50` | Long moving average period (bars) |
| `broker` | `alpaca` | `alpaca` or `trading212` |
| `trade_amount` | `20` | Cash per buy, in the account currency (fractional shares) |
| `starting_cash` | `260` | Simulated account size, in the account currency |
| `currency_symbol` | `$` | Shown in the log for cash amounts |
| `bar_minutes` | `15` | Bar size in intraday mode |
| `lookback_days` | `100` | Trading days of history in daily mode |
| `alpaca_base_url` | paper API | Alpaca trading endpoint (`https://api.alpaca.markets` for live) |
| `alpaca_data_url` | Alpaca data API | Market data endpoint |
| `t212_base_url` | demo API | Trading 212 endpoint (`https://live.trading212.com/api/v0` for live) |
| `log_file`, `state_file` | per broker | Where the log and simulated account are stored |

### Default watchlist

Spread across all 11 S&P sectors plus broad-market ETFs:

| Sector | Symbols |
|---|---|
| Technology / Communication | AAPL, MSFT, NVDA, GOOGL, META |
| Consumer discretionary | AMZN, HD |
| Consumer staples | WMT, COST, KO |
| Financials | JPM, V |
| Health care | UNH, LLY, JNJ |
| Energy | XOM, CVX |
| Industrials | CAT |
| Utilities | NEE |
| Materials | LIN |
| Real estate | AMT |
| Index ETFs | SPY (S&P 500), QQQ (Nasdaq 100), IWM (small caps), DIA (Dow) |

Symbols are processed in list order, so when cash is short, earlier symbols get priority.

## Running

```bash
python trading_bot.py                          # Alpaca: one pass over every symbol
python trading_bot.py config_trading212.json   # Trading 212: one pass over every symbol
python test_alpaca_connection.py    # show the Alpaca account's buying power and positions
```

When the market is closed (including holidays) the bot exits without doing anything. If one symbol fails (bad ticker, data error), the others still run and the bot exits with an error code.

## Running automatically on this machine

```bash
python run_local.py
```

Leave it running: it runs the Alpaca bot and then the Trading 212 bot one minute after every 15-minute mark (9:31, 9:46, ... ET) on weekdays during US market hours (14:30–21:00 UK most of the year), and sleeps the rest of the time. Each bot runs as its own process, so one failing never stops the other. Stop it with Ctrl+C.

The computer must stay on and awake during market hours (disable sleep, or the runs are missed). A missed run doesn't lose a crossover: each bot remembers each symbol's last above/below state, so the next run catches it, just later.

## Trading 212 notes

- Orders are by share quantity, so the bot converts £ amounts to fractional shares using the latest USD price and the GBP/USD rate (from Yahoo Finance). If a quantity is rejected for precision, it retries with fewer decimals; a rejected order is never placed, so this can't double-buy.
- Trading 212 market orders aren't idempotent, so the bot never retries an order after a timeout or server error.
- US-listed ETFs (SPY, QQQ, IWM, DIA) aren't available to UK retail investors, so they're left out of the Trading 212 watchlist.

## Logs

Each bot appends one line per symbol plus a summary and account line to its log file, e.g.:

```
=== Summary: KO: BUY $20.00 | V: sell skipped (not holding) ===
=== Account: settled $240.00 + unsettled $0.00 + positions $20.00 = $260.00 (started $260.00) ===
```

Logs and state files stay on this machine (in `logs/` and `state/`).
