"""
Get REAL P&L by querying Binance transaction history
Calculates: Total Deposited - Current Equity = P&L
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.binance_client import BinanceClient


async def get_flexible_loan_borrow_history(client: BinanceClient) -> list:
    """Get all flexible loan borrow history"""
    all_rows = []
    # Get last 90 days
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)

    params = {
        'startTime': start_time,
        'endTime': end_time,
        'limit': 100
    }
    result = await client._request('GET', '/sapi/v2/loan/flexible/borrow/history', params)
    if 'rows' in result:
        all_rows.extend(result['rows'])
    return all_rows


async def get_ltv_adjustment_history(client: BinanceClient) -> list:
    """Get LTV adjustment history (collateral additions)"""
    all_rows = []
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)

    params = {
        'startTime': start_time,
        'endTime': end_time,
        'limit': 100
    }
    result = await client._request('GET', '/sapi/v2/loan/flexible/ltv/adjustment/history', params)
    if 'rows' in result:
        all_rows.extend(result['rows'])
    return all_rows


async def get_deposit_history(client: BinanceClient) -> list:
    """Get deposit history"""
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)

    params = {
        'startTime': start_time,
        'endTime': end_time,
        'status': 1  # Success only
    }
    result = await client._request('GET', '/sapi/v1/capital/deposit/hisrec', params)
    return result if isinstance(result, list) else []


async def get_current_equity(client: BinanceClient) -> dict:
    """Calculate current total equity"""
    loans = await client.get_flexible_loan_ongoing_orders()

    total_collateral = 0.0
    total_debt = 0.0

    for loan in loans:
        coll_coin = loan.get('collateralCoin', '')
        loan_coin = loan.get('loanCoin', '')
        coll_amount = float(loan.get('collateralAmount', 0))
        total_debt_amount = float(loan.get('totalDebt', 0))

        # Get prices
        if coll_coin == 'USDT':
            coll_price = 1.0
        else:
            coll_price = await client.get_price(f'{coll_coin}USDT')

        if loan_coin == 'USDT':
            loan_price = 1.0
        else:
            loan_price = await client.get_price(f'{loan_coin}USDT')

        total_collateral += coll_amount * coll_price
        total_debt += total_debt_amount * loan_price

    # Get spot balances
    spot_value = 0.0
    balances = await client.get_all_spot_balances()
    for asset, amount in balances.items():
        if asset.startswith('LD'):
            continue
        if asset == 'USDT':
            spot_value += amount
        else:
            try:
                price = await client.get_price(f'{asset}USDT')
                spot_value += amount * price
            except:
                pass

    return {
        'total_collateral': total_collateral,
        'total_debt': total_debt,
        'spot_value': spot_value,
        'net_equity': (total_collateral - total_debt) + spot_value
    }


async def main():
    print("\n" + "="*70)
    print("     LEVERAGE LOOPER - REAL P&L FROM BINANCE HISTORY")
    print("="*70 + "\n")

    client = BinanceClient()
    await client.initialize()

    try:
        # Get current equity
        print("Fetching current equity...")
        equity = await get_current_equity(client)

        print(f"\n  CURRENT EQUITY: ${equity['net_equity']:,.2f}")
        print(f"  ├─ Collateral:  ${equity['total_collateral']:,.2f}")
        print(f"  ├─ Debt:        ${equity['total_debt']:,.2f}")
        print(f"  └─ Spot:        ${equity['spot_value']:,.2f}")

        # Get borrow history to find initial collateral deposits
        print("\n" + "-"*70)
        print("  LOAN BORROW HISTORY (Initial Collateral Deposits)")
        print("-"*70)

        borrow_history = await get_flexible_loan_borrow_history(client)

        total_initial_collateral = 0.0
        initial_deposits_by_coin = {}

        for record in borrow_history:
            coll_coin = record.get('collateralCoin', '')
            coll_amount = float(record.get('initialCollateralAmount', 0))
            timestamp = int(record.get('borrowTime', 0))
            dt = datetime.fromtimestamp(timestamp / 1000)

            # Get price at current time (approximation)
            if coll_coin == 'USDT':
                price = 1.0
            else:
                try:
                    price = await client.get_price(f'{coll_coin}USDT')
                except:
                    price = 0

            usd_value = coll_amount * price
            total_initial_collateral += usd_value

            if coll_coin not in initial_deposits_by_coin:
                initial_deposits_by_coin[coll_coin] = 0
            initial_deposits_by_coin[coll_coin] += coll_amount

            print(f"  {dt.strftime('%Y-%m-%d %H:%M')} | {coll_amount:>12.4f} {coll_coin:<6} | ~${usd_value:>10.2f}")

        # Get LTV adjustments (additional collateral added by the bot)
        print("\n" + "-"*70)
        print("  LTV ADJUSTMENTS (Bot's Collateral Additions)")
        print("-"*70)

        ltv_history = await get_ltv_adjustment_history(client)

        total_bot_additions = 0.0
        for record in ltv_history:
            direction = record.get('direction', '')
            if direction != 'ADDITIONAL':
                continue

            coll_coin = record.get('collateralCoin', '')
            amount = float(record.get('adjustmentAmount', 0))
            timestamp = int(record.get('adjustTime', 0))
            dt = datetime.fromtimestamp(timestamp / 1000)

            if coll_coin == 'USDT':
                price = 1.0
            else:
                try:
                    price = await client.get_price(f'{coll_coin}USDT')
                except:
                    price = 0

            usd_value = amount * price
            total_bot_additions += usd_value

            print(f"  {dt.strftime('%Y-%m-%d %H:%M')} | +{amount:>11.4f} {coll_coin:<6} | ~${usd_value:>10.2f}")

        if not ltv_history:
            print("  (No LTV adjustments found)")

        # Get deposits to Binance
        print("\n" + "-"*70)
        print("  BINANCE DEPOSIT HISTORY")
        print("-"*70)

        deposits = await get_deposit_history(client)

        total_deposits = 0.0
        for dep in deposits:
            coin = dep.get('coin', '')
            amount = float(dep.get('amount', 0))
            timestamp = int(dep.get('insertTime', 0))
            dt = datetime.fromtimestamp(timestamp / 1000)

            if coin == 'USDT':
                price = 1.0
            else:
                try:
                    price = await client.get_price(f'{coin}USDT')
                except:
                    price = 0

            usd_value = amount * price
            total_deposits += usd_value

            print(f"  {dt.strftime('%Y-%m-%d %H:%M')} | {amount:>12.4f} {coin:<6} | ~${usd_value:>10.2f}")

        if not deposits:
            print("  (No deposits in last 90 days)")

        # Calculate P&L
        print("\n" + "="*70)
        print("     P&L CALCULATION")
        print("="*70)

        # Initial investment = initial collateral from borrows (what you put in)
        # Note: Bot additions are from borrowed funds, not new deposits

        print(f"\n  Initial Collateral (from loans): ${total_initial_collateral:,.2f}")
        print(f"  Bot Additions (from borrows):    ${total_bot_additions:,.2f}")
        print(f"  External Deposits:               ${total_deposits:,.2f}")
        print(f"  Current Net Equity:              ${equity['net_equity']:,.2f}")

        # The true input is what you deposited as initial collateral
        # P&L = Current Equity - Initial Collateral Deposited
        pnl = equity['net_equity'] - total_initial_collateral
        pnl_pct = (pnl / total_initial_collateral * 100) if total_initial_collateral > 0 else 0

        print("\n" + "-"*70)
        if pnl >= 0:
            print(f"  P&L: +${pnl:,.2f} (+{pnl_pct:.2f}%)")
            print("\n  STATUS: PROFIT - You ARE making money!")
        else:
            print(f"  P&L: -${abs(pnl):,.2f} ({pnl_pct:.2f}%)")
            print("\n  STATUS: LOSS - You are NOT making money!")
        print("-"*70)

        print("\n  NOTE: P&L = Current Equity - Initial Collateral Deposited")
        print("        Bot additions come from borrowed funds, not your pocket.")
        print("="*70 + "\n")

        return {
            'current_equity': equity['net_equity'],
            'initial_collateral': total_initial_collateral,
            'bot_additions': total_bot_additions,
            'pnl': pnl,
            'pnl_percent': pnl_pct
        }

    finally:
        await client.close()


if __name__ == "__main__":
    result = asyncio.run(main())
