#!/usr/bin/env python3
"""
Test connection to Alpaca paper trading account.
Retrieves account information and positions.
"""

import requests
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
        # Alpaca API endpoints (paper trading)
        base_url = "https://paper-api.alpaca.markets"
        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key
        }

        # Get account information
        account_response = requests.get(f"{base_url}/v2/account", headers=headers)
        account_response.raise_for_status()
        account = account_response.json()

        # Get positions
        positions_response = requests.get(f"{base_url}/v2/positions", headers=headers)
        positions_response.raise_for_status()
        positions = positions_response.json()

        print("=" * 60)
        print("ALPACA PAPER TRADING ACCOUNT - CONNECTION TEST")
        print("=" * 60)
        print(f"\n✓ Connection successful!\n")

        print("ACCOUNT INFORMATION:")
        print(f"  Account Status: {account.get('status', 'N/A')}")
        print(f"  Account Type: {account.get('account_type', 'N/A')}")
        print(f"\nBUYING POWER:")
        print(f"  Buying Power: ${float(account.get('buying_power', 0)):,.2f}")
        print(f"  Cash: ${float(account.get('cash', 0)):,.2f}")
        print(f"  Portfolio Value: ${float(account.get('portfolio_value', 0)):,.2f}")
        print(f"  Day Trading Buying Power: ${float(account.get('daytrading_buying_power', 0)):,.2f}")

        print(f"\nPOSITIONS ({len(positions)} position(s)):")
        if positions:
            for position in positions:
                qty = float(position.get('qty', 0))
                current_price = float(position.get('current_price', 0))
                market_value = float(position.get('market_value', 0))
                unrealized_pl = float(position.get('unrealized_pl', 0))
                unrealized_plpc = float(position.get('unrealized_plpc', 0))

                print(f"\n  Symbol: {position.get('symbol')}")
                print(f"    Quantity: {qty}")
                print(f"    Current Price: ${current_price:,.2f}")
                print(f"    Current Value: ${market_value:,.2f}")
                print(f"    Unrealized P/L: ${unrealized_pl:,.2f} ({unrealized_plpc*100:.2f}%)")
        else:
            print("  No positions held")

        print("\n" + "=" * 60)
        return True

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            print(f"ERROR: Invalid API credentials (401 Unauthorized)")
            print("Please check your APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env")
        else:
            print(f"ERROR: HTTP Error {e.response.status_code}: {e}")
        return False
    except Exception as e:
        print(f"ERROR: Failed to connect to Alpaca: {e}")
        return False

if __name__ == "__main__":
    success = test_alpaca_connection()
    exit(0 if success else 1)
