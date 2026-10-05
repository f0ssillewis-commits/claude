# Alpaca Paper Trading Test

This project tests the connection to your Alpaca paper trading account and retrieves account information.

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Get Your Alpaca API Credentials

1. Go to [Alpaca Dashboard](https://app.alpaca.markets/brokerage/account/api)
2. Under "API Keys", create a new key or copy your existing one
3. You'll need:
   - **API Key ID** (your API key)
   - **Secret Key** (your secret key)

### 3. Set Up Environment Variables

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Then edit `.env` and add your credentials:

```
APCA_API_KEY_ID=your_actual_api_key_here
APCA_API_SECRET_KEY=your_actual_secret_key_here
```

**Important**: Never commit `.env` to git - it contains sensitive credentials!

### 4. Run the Test

```bash
python test_alpaca_connection.py
```

## What It Does

The script will:
- Connect to your Alpaca paper trading account
- Display your account status
- Show your **buying power** (how much you can invest)
- List all **stocks you currently hold** (positions)
- Show current prices and profit/loss for each position

## Example Output

```
============================================================
ALPACA PAPER TRADING ACCOUNT - CONNECTION TEST
============================================================

✓ Connection successful!

ACCOUNT INFORMATION:
  Account Status: ACTIVE
  Account Type: trading

BUYING POWER:
  Buying Power: $25,000.00
  Cash: $25,000.00
  Portfolio Value: $25,000.00
  Day Trading Buying Power: $25,000.00

POSITIONS (0 position(s)):
  No positions held

============================================================
```

## Troubleshooting

- **"API credentials not found"**: Make sure your `.env` file exists and has the correct variable names
- **"Invalid credentials"**: Double-check your API key and secret key from the Alpaca dashboard
- **"Connection refused"**: Make sure you have internet connection and Alpaca API is accessible
