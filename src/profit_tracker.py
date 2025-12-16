"""
Profit Tracker - Tracks P&L for leverage looping positions

Calculates:
- Net Equity = Total Collateral Value - Total Debt Value
- P&L since bot started (absolute and %)
- Historical equity snapshots for trend analysis
"""
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional
from loguru import logger

DATA_FILE = "data/profit_history.json"


class ProfitTracker:
    """Tracks profit/loss over time for leverage positions"""

    def __init__(self, client):
        self.client = client
        self.history: List[Dict] = []
        self.starting_equity: Optional[float] = None
        self.starting_timestamp: Optional[str] = None
        self._load_history()

    def _ensure_data_dir(self):
        """Ensure data directory exists"""
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

    def _load_history(self):
        """Load historical data from file"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r') as f:
                    data = json.load(f)
                    self.history = data.get('history', [])
                    self.starting_equity = data.get('starting_equity')
                    self.starting_timestamp = data.get('starting_timestamp')
                    logger.info(f"Loaded {len(self.history)} historical snapshots")
                    if self.starting_equity:
                        logger.info(f"Starting equity: ${self.starting_equity:.2f} from {self.starting_timestamp}")
        except Exception as e:
            logger.warning(f"Could not load profit history: {e}")
            self.history = []

    def _save_history(self):
        """Save historical data to file"""
        try:
            self._ensure_data_dir()
            with open(DATA_FILE, 'w') as f:
                json.dump({
                    'starting_equity': self.starting_equity,
                    'starting_timestamp': self.starting_timestamp,
                    'history': self.history[-1000:]  # Keep last 1000 snapshots
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save profit history: {e}")

    async def _get_price(self, coin: str) -> float:
        """Get price for a coin (1.0 for USDT)"""
        if coin == 'USDT':
            return 1.0
        try:
            return await self.client.get_price(f'{coin}USDT')
        except Exception as e:
            logger.warning(f"Could not get price for {coin}: {e}")
            return 0.0

    async def calculate_position_equity(self, loan: Dict) -> Dict:
        """
        Calculate equity for a single position

        Returns:
            {
                position: str,
                collateral_coin: str,
                loan_coin: str,
                collateral_amount: float,
                collateral_usd: float,
                debt_amount: float,
                debt_usd: float,
                equity_usd: float,
                leverage: float,
                ltv: float
            }
        """
        coll_coin = loan.get('collateralCoin', '')
        loan_coin = loan.get('loanCoin', '')
        coll_amount = float(loan.get('collateralAmount', 0))
        total_debt = float(loan.get('totalDebt', 0))
        current_ltv = float(loan.get('currentLTV', 0))

        coll_price = await self._get_price(coll_coin)
        loan_price = await self._get_price(loan_coin)

        coll_usd = coll_amount * coll_price
        debt_usd = total_debt * loan_price
        equity_usd = coll_usd - debt_usd

        leverage = coll_usd / equity_usd if equity_usd > 0 else 0

        return {
            'position': f"{coll_coin}->{loan_coin}",
            'collateral_coin': coll_coin,
            'loan_coin': loan_coin,
            'collateral_amount': coll_amount,
            'collateral_usd': coll_usd,
            'debt_amount': total_debt,
            'debt_usd': debt_usd,
            'equity_usd': equity_usd,
            'leverage': leverage,
            'ltv': current_ltv * 100
        }

    async def calculate_total_equity(self) -> Dict:
        """
        Calculate total equity across all positions + spot balances

        Returns:
            {
                total_equity_usd: float,
                total_collateral_usd: float,
                total_debt_usd: float,
                spot_value_usd: float,
                positions: List[Dict],
                timestamp: str
            }
        """
        loans = await self.client.get_flexible_loan_ongoing_orders()

        total_collateral = 0.0
        total_debt = 0.0
        positions = []

        for loan in loans:
            pos = await self.calculate_position_equity(loan)
            positions.append(pos)
            total_collateral += pos['collateral_usd']
            total_debt += pos['debt_usd']

        # Also get spot balances
        spot_value = 0.0
        try:
            balances = await self.client.get_all_spot_balances()
            for asset, amount in balances.items():
                if asset.startswith('LD'):  # Skip Simple Earn tokens
                    continue
                price = await self._get_price(asset)
                spot_value += amount * price
        except Exception as e:
            logger.warning(f"Could not get spot balances: {e}")

        total_equity = (total_collateral - total_debt) + spot_value

        return {
            'total_equity_usd': total_equity,
            'total_collateral_usd': total_collateral,
            'total_debt_usd': total_debt,
            'spot_value_usd': spot_value,
            'positions': positions,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

    async def record_snapshot(self) -> Dict:
        """
        Record current equity snapshot and calculate P&L

        Returns full snapshot with P&L calculations
        """
        equity_data = await self.calculate_total_equity()

        # Set starting equity if this is first snapshot
        if self.starting_equity is None:
            self.starting_equity = equity_data['total_equity_usd']
            self.starting_timestamp = equity_data['timestamp']
            logger.info(f"Set starting equity: ${self.starting_equity:.2f}")

        # Calculate P&L
        current_equity = equity_data['total_equity_usd']
        pnl_absolute = current_equity - self.starting_equity
        pnl_percent = (pnl_absolute / self.starting_equity * 100) if self.starting_equity > 0 else 0

        # Calculate P&L since last snapshot
        pnl_since_last = 0.0
        if self.history:
            last_equity = self.history[-1].get('total_equity_usd', current_equity)
            pnl_since_last = current_equity - last_equity

        snapshot = {
            **equity_data,
            'starting_equity_usd': self.starting_equity,
            'starting_timestamp': self.starting_timestamp,
            'pnl_absolute_usd': pnl_absolute,
            'pnl_percent': pnl_percent,
            'pnl_since_last_usd': pnl_since_last
        }

        # Store in history
        self.history.append({
            'timestamp': equity_data['timestamp'],
            'total_equity_usd': current_equity,
            'total_collateral_usd': equity_data['total_collateral_usd'],
            'total_debt_usd': equity_data['total_debt_usd'],
            'spot_value_usd': equity_data['spot_value_usd'],
            'pnl_absolute_usd': pnl_absolute,
            'pnl_percent': pnl_percent
        })

        self._save_history()

        return snapshot

    def get_pnl_summary(self) -> Dict:
        """
        Get P&L summary from historical data

        Returns:
            {
                starting_equity: float,
                starting_timestamp: str,
                current_equity: float,
                pnl_absolute: float,
                pnl_percent: float,
                high_equity: float,
                low_equity: float,
                snapshots_count: int,
                trend: str (up/down/flat)
            }
        """
        if not self.history:
            return {
                'starting_equity': self.starting_equity,
                'starting_timestamp': self.starting_timestamp,
                'current_equity': None,
                'pnl_absolute': 0,
                'pnl_percent': 0,
                'high_equity': None,
                'low_equity': None,
                'snapshots_count': 0,
                'trend': 'unknown'
            }

        equities = [h['total_equity_usd'] for h in self.history]
        current = equities[-1]

        # Determine trend from last 5 snapshots
        trend = 'flat'
        if len(equities) >= 2:
            recent = equities[-5:] if len(equities) >= 5 else equities
            if recent[-1] > recent[0] * 1.001:  # >0.1% gain
                trend = 'up'
            elif recent[-1] < recent[0] * 0.999:  # >0.1% loss
                trend = 'down'

        return {
            'starting_equity': self.starting_equity,
            'starting_timestamp': self.starting_timestamp,
            'current_equity': current,
            'pnl_absolute': current - self.starting_equity if self.starting_equity else 0,
            'pnl_percent': ((current - self.starting_equity) / self.starting_equity * 100) if self.starting_equity else 0,
            'high_equity': max(equities),
            'low_equity': min(equities),
            'snapshots_count': len(self.history),
            'trend': trend
        }

    def get_history(self, limit: int = 100) -> List[Dict]:
        """Get recent history snapshots"""
        return self.history[-limit:]

    def reset_tracking(self):
        """Reset all tracking data (start fresh)"""
        self.history = []
        self.starting_equity = None
        self.starting_timestamp = None
        self._save_history()
        logger.info("Profit tracking reset")
