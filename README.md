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

The signal uses daily closes, so run the bot once per trading day (e.g. via cron shortly after market open). Orders are market orders with `day` time-in-force; if placed while the market is closed they queue for the next open.
