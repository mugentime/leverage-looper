"""
Check current P&L status - Run this to see if you're making money!

Usage: python scripts/check_pnl.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.binance_client import BinanceClient
from src.profit_tracker import ProfitTracker


async def check_pnl():
    """Check current P&L and display results"""
    print("\n" + "="*60)
    print("  LEVERAGE LOOPER - P&L STATUS CHECK")
    print("="*60 + "\n")

    client = BinanceClient()
    await client.initialize()

    try:
        tracker = ProfitTracker(client)

        # Get current snapshot
        snapshot = await tracker.record_snapshot()
        summary = tracker.get_pnl_summary()

        # Display results
        equity = snapshot['total_equity_usd']
        starting = snapshot['starting_equity_usd']
        pnl = snapshot['pnl_absolute_usd']
        pnl_pct = snapshot['pnl_percent']

        print(f"  Starting Equity:  ${starting:,.2f}")
        print(f"  Current Equity:   ${equity:,.2f}")
        print()

        if pnl >= 0:
            print(f"  P&L:              +${pnl:,.2f} (+{pnl_pct:.2f}%)")
            print()
            print("  STATUS: PROFIT - You ARE making money!")
        else:
            print(f"  P&L:              -${abs(pnl):,.2f} ({pnl_pct:.2f}%)")
            print()
            print("  STATUS: LOSS - You are NOT making money")

        print()
        print("-"*60)
        print("  BREAKDOWN:")
        print("-"*60)
        print(f"  Total Collateral: ${snapshot['total_collateral_usd']:,.2f}")
        print(f"  Total Debt:       ${snapshot['total_debt_usd']:,.2f}")
        print(f"  Spot Balance:     ${snapshot['spot_value_usd']:,.2f}")
        print()

        if summary['high_equity'] and summary['low_equity']:
            print(f"  High Equity:      ${summary['high_equity']:,.2f}")
            print(f"  Low Equity:       ${summary['low_equity']:,.2f}")
            print(f"  Trend:            {summary['trend'].upper()}")
            print()

        print("-"*60)
        print("  POSITIONS:")
        print("-"*60)

        for pos in snapshot['positions']:
            lev = pos['leverage']
            eq = pos['equity_usd']
            print(f"  {pos['position']:<15} | Equity: ${eq:>10,.2f} | Leverage: {lev:.2f}x | LTV: {pos['ltv']:.1f}%")

        print()
        print(f"  Snapshots recorded: {summary['snapshots_count']}")
        print(f"  Started tracking:   {snapshot['starting_timestamp']}")
        print()
        print("="*60 + "\n")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(check_pnl())
