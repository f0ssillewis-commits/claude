#!/usr/bin/env python3
"""
Test connection to Alpaca paper trading account.
Retrieves account information and positions.
"""

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def test_alpaca_connection():
    """Test connection to Alpaca paper trading account."""

    # Get API credentials from environment variables
    api_key = os.getenv('APCA_API_KEY_ID')
    secret_key = os.getenv('APCA_API_SECRET_KEY')

    if not api_key or not secret_key:
        print("ERROR: API credentials not found in environment variables.")
        print("Please set APCA_API_KEY_ID and APCA_API_SECRET_KEY")
        print("\nYou can add them to a .env file in the project root:")
        print("APCA_API_KEY_ID=your_api_key")
        print("APCA_API_SECRET_KEY=your_secret_key")
        return False

    try:
        # Create Alpaca trading client (paper trading by default)
        client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)

        # Get account information
        account = client.get_account()

        print("=" * 60)
        print("ALPACA PAPER TRADING ACCOUNT - CONNECTION TEST")
        print("=" * 60)
        print(f"\n✓ Connection successful!\n")

        print("ACCOUNT INFORMATION:")
        print(f"  Account Status: {account.status}")
        print(f"  Account Type: {account.account_type}")
        print(f"\nBUYING POWER:")
        print(f"  Buying Power: ${account.buying_power:,.2f}")
        print(f"  Cash: ${account.cash:,.2f}")
        print(f"  Portfolio Value: ${account.portfolio_value:,.2f}")
        print(f"  Day Trading Buying Power: ${account.daytrading_buying_power:,.2f}")

        # Get positions (stocks held)
        positions = client.get_all_positions()

        print(f"\nPOSITIONS ({len(positions)} position(s)):")
        if positions:
            for position in positions:
                print(f"\n  Symbol: {position.symbol}")
                print(f"    Quantity: {position.qty}")
                print(f"    Current Price: ${position.current_price:,.2f}")
                print(f"    Current Value: ${position.market_value:,.2f}")
                print(f"    Unrealized P/L: ${position.unrealized_pl:,.2f} ({position.unrealized_plpc*100:.2f}%)")
        else:
            print("  No positions held")

        print("\n" + "=" * 60)
        return True

    except Exception as e:
        print(f"ERROR: Failed to connect to Alpaca: {e}")
        return False

if __name__ == "__main__":
    success = test_alpaca_connection()
    exit(0 if success else 1)
