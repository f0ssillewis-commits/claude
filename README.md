# Alpaca Moving Average Crossover Bot

Trades a single stock on an Alpaca **paper** account using a moving average crossover:

- **Buy** when the short MA crosses *above* the long MA between the last two trading days
- **Sell** when the short MA crosses *below* the long MA between the last two trading days

The bot is long-only: it skips a buy if you already hold the stock and skips a sell if you don't hold enough shares (so it never opens a short).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then put your Alpaca paper API key and secret in .env
```

`.env` is git-ignored, so your credentials stay on your machine.

## Configuration (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `symbol` | `AAPL` | Stock to trade |
| `short_window` | `20` | Short moving average period (days) |
| `long_window` | `50` | Long moving average period (days) |
| `trade_quantity` | `1` | Shares per order |
| `lookback_days` | `100` | Trading days of history fetched from yfinance |
| `alpaca_base_url` | paper API | Alpaca endpoint |

## Running

```bash
python trading_bot.py               # check for a crossover and trade if one occurred
python test_alpaca_connection.py    # show buying power and positions
```

The signal compares the last two *completed* trading days (today's in-progress price is ignored before the 4pm close). Orders are market orders with `day` time-in-force. On market holidays the bot logs that the market is closed and does nothing.

## Automated schedule (GitHub Actions)

`.github/workflows/trading-bot.yml` runs the bot every Monday-Friday at 9:30 AM ET (handles daylight saving automatically). It needs two repository secrets, added under **Settings → Secrets and variables → Actions**:

- `APCA_API_KEY_ID`
- `APCA_API_SECRET_KEY`

You can also trigger a run manually from the **Actions** tab ("Trading bot" → "Run workflow"). GitHub's scheduler can start runs a few minutes late.

## Logs

Every run appends to `logs/trading_bot.log`, which the workflow commits back to the repository, so you can read the full history on GitHub. Each run's console output is also visible in the Actions tab.
