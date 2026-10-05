# Alpaca Moving Average Crossover Bot

Paper-trades a list of stocks/ETFs on Alpaca as a rehearsal for a **small live account (~$260)**. Each symbol gets the same moving average crossover:

- **Buy** a fixed dollar amount when the short MA crosses *above* the long MA
- **Sell** the whole position when the short MA crosses *below* the long MA

The bot is long-only: it skips a buy if it already holds the symbol and skips a sell if it holds nothing (it never opens a short).

## Simulated small cash account

Your paper account has far more buying power than a real £200 account, so the bot keeps its own budget in `state/<mode>.json` and only trades within it:

- Starts with `starting_cash_usd` (default $260) and buys `trade_amount_usd` (default $20) per signal using fractional shares.
- No margin: a buy is skipped if there isn't enough **settled** cash.
- Sale proceeds settle the next trading day (T+1), like a real cash account, and can't be reused until then.

Delete `state/<mode>.json` to reset the budget.

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
cp .env.example .env   # then put your Alpaca paper API key and secret in .env
```

`.env` is git-ignored, so your credentials stay on your machine.

## Configuration (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `mode` | `intraday` | `intraday` or `daily` |
| `symbols` | 25 symbols (see below) | Stocks/ETFs to monitor and trade |
| `short_window` | `20` | Short moving average period (bars) |
| `long_window` | `50` | Long moving average period (bars) |
| `trade_amount_usd` | `20` | Dollars per buy (fractional shares) |
| `starting_cash_usd` | `260` | Simulated account size |
| `bar_minutes` | `15` | Bar size in intraday mode |
| `lookback_days` | `100` | Trading days of history in daily mode |
| `alpaca_base_url` | paper API | Alpaca trading endpoint (`https://api.alpaca.markets` for live) |
| `alpaca_data_url` | Alpaca data API | Market data endpoint |

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
python trading_bot.py               # one pass: check every symbol, trade on crossovers
python test_alpaca_connection.py    # show the Alpaca account's buying power and positions
```

When the market is closed (including holidays) the bot exits without doing anything. If one symbol fails (bad ticker, data error), the others still run and the run is marked failed in GitHub Actions.

## Automated schedule (GitHub Actions)

`.github/workflows/trading-bot.yml` runs the bot every 15 minutes on weekdays across market hours (daylight saving handled automatically). It needs two repository secrets, added under **Settings → Secrets and variables → Actions**:

- `APCA_API_KEY_ID`
- `APCA_API_SECRET_KEY`

You can also trigger a run manually from the **Actions** tab ("Trading bot" → "Run workflow"). GitHub's scheduler often starts runs late and occasionally skips them; that's acceptable for paper testing, but a live account should run on an always-on machine instead.

## Logs

Every run appends one line per symbol plus a summary and account line to `logs/trading_bot.log`, e.g.:

```
=== Summary: KO: BUY $20.00 | V: sell skipped (not holding) ===
=== Account: settled $240.00 + unsettled $0.00 + positions $20.00 = $260.00 (started $260.00) ===
```

The workflow commits the log and `state/` back to the repository after each market-hours run, so you can read the full history on GitHub.
