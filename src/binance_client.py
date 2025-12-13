"""
Binance API Client Wrapper for Simple Earn and Crypto Loans

Uses direct REST API calls for Simple Earn and Crypto Loans
(python-binance doesn't have these methods)
"""
import asyncio
import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
from binance import AsyncClient
from loguru import logger

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import settings


class BinanceClient:
    """
    Wrapper for Binance API with methods for:
    - Simple Earn (Flexible products) - via direct REST API
    - Crypto Loans (Flexible loans) - via direct REST API
    - Spot trading (via python-binance)
    - Price data (via python-binance)
    """

    BASE_URL = "https://api.binance.com"

    def __init__(self):
        self.client: Optional[AsyncClient] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._initialized = False
        self._api_key = settings.BINANCE_API_KEY
        self._api_secret = settings.BINANCE_API_SECRET
        self._exchange_info_cache: Dict[str, Dict] = {}  # symbol -> {stepSize, minQty, minNotional}

    def _sign(self, params: Dict) -> str:
        """Generate HMAC SHA256 signature"""
        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_server_time(self) -> int:
        """Get Binance server time"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/api/v3/time"
        async with session.get(url) as resp:
            data = await resp.json()
            return data.get('serverTime', int(time.time() * 1000))

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Dict = None,
        signed: bool = True
    ) -> Dict:
        """Make signed API request"""
        session = await self._get_session()
        url = f"{self.BASE_URL}{endpoint}"

        if params is None:
            params = {}

        if signed:
            # Use server time to avoid timestamp errors
            server_time = await self._get_server_time()
            params['timestamp'] = server_time
            params['signature'] = self._sign(params)

        headers = {'X-MBX-APIKEY': self._api_key}

        try:
            if method == 'GET':
                async with session.get(url, params=params, headers=headers) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        logger.error(f"API error {resp.status}: {data}")
                    return data
            elif method == 'POST':
                async with session.post(url, params=params, headers=headers) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        logger.error(f"API error {resp.status}: {data}")
                    return data
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise

    async def initialize(self) -> None:
        """Initialize the async client"""
        if self._initialized:
            return

        self.client = await AsyncClient.create(
            api_key=self._api_key,
            api_secret=self._api_secret,
            testnet=False
        )
        self._initialized = True
        logger.info("Binance client initialized")

    async def close(self) -> None:
        """Close the client connection"""
        if self.client:
            await self.client.close_connection()
        if self._session and not self._session.closed:
            await self._session.close()
        self._initialized = False
        logger.info("Binance client closed")

    # ==================== CRYPTO LOANS METHODS (Direct API) ====================

    async def get_flexible_loan_ongoing_orders(
        self,
        loan_coin: str = None,
        collateral_coin: str = None
    ) -> List[Dict]:
        """Get ongoing flexible loan orders"""
        params = {'limit': 100}  # Fetch up to 100 loans (default was too low)
        if loan_coin:
            params['loanCoin'] = loan_coin
        if collateral_coin:
            params['collateralCoin'] = collateral_coin

        result = await self._request('GET', '/sapi/v2/loan/flexible/ongoing/orders', params)
        return result.get('rows', [])

    async def borrow_flexible_loan_by_amount(
        self,
        loan_coin: str,
        collateral_coin: str,
        loan_amount: float
    ) -> Dict:
        """Borrow using flexible loan by specifying loan amount"""
        # Format amount as string with proper precision (Binance API requires string)
        formatted_amount = f"{loan_amount:.8f}".rstrip('0').rstrip('.')

        params = {
            'loanCoin': loan_coin,
            'collateralCoin': collateral_coin,
            'loanAmount': formatted_amount
        }
        result = await self._request('POST', '/sapi/v2/loan/flexible/borrow', params)
        logger.info(f"Borrowed {loan_amount} {loan_coin} against {collateral_coin}")
        return result

    async def adjust_loan_ltv(
        self,
        loan_coin: str,
        collateral_coin: str,
        adjustment_amount: float,
        direction: str  # "ADDITIONAL" or "REDUCED"
    ) -> Dict:
        """Adjust loan LTV by adding or removing collateral"""
        # Format amount as string with proper precision (Binance API requires string)
        formatted_amount = f"{adjustment_amount:.8f}".rstrip('0').rstrip('.')

        params = {
            'loanCoin': loan_coin,
            'collateralCoin': collateral_coin,
            'adjustmentAmount': formatted_amount,
            'direction': direction
        }
        result = await self._request('POST', '/sapi/v2/loan/flexible/adjust/ltv', params)
        logger.info(f"Adjusted LTV: {direction} {adjustment_amount} {collateral_coin}")
        return result

    # ==================== SPOT TRADING METHODS (via python-binance) ====================

    async def get_spot_balance(self, asset: str) -> float:
        """Get spot balance for an asset"""
        try:
            account = await self.client.get_account()
            for balance in account.get("balances", []):
                if balance["asset"] == asset:
                    return float(balance["free"])
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get spot balance for {asset}: {e}")
            return 0.0

    async def market_buy(self, symbol: str, quote_qty: float) -> Dict:
        """Market buy with quote quantity (e.g., buy SOL with 100 USDT)"""
        try:
            result = await self.client.order_market_buy(
                symbol=symbol,
                quoteOrderQty=quote_qty
            )
            logger.info(f"Market buy {symbol} for {quote_qty} quote currency")
            return result
        except Exception as e:
            logger.error(f"Failed to market buy {symbol}: {e}")
            raise

    async def market_sell(self, symbol: str, quantity: float) -> Dict:
        """Market sell with base quantity"""
        try:
            result = await self.client.order_market_sell(
                symbol=symbol,
                quantity=quantity
            )
            logger.info(f"Market sell {quantity} {symbol}")
            return result
        except Exception as e:
            logger.error(f"Failed to market sell {symbol}: {e}")
            raise

    # ==================== CONVERT/SWAP API (no minimum) ====================

    async def get_convert_quote(self, from_asset: str, to_asset: str, from_amount: float) -> Dict:
        """Get a quote for converting one asset to another using Binance Convert API"""
        # Truncate to 8 decimal places (Binance max precision)
        truncated_amount = int(from_amount * 100_000_000) / 100_000_000
        params = {
            'fromAsset': from_asset,
            'toAsset': to_asset,
            'fromAmount': f"{truncated_amount:.8f}".rstrip('0').rstrip('.'),
            'walletType': 'SPOT',
            'validTime': '10s'
        }
        result = await self._request('POST', '/sapi/v1/convert/getQuote', params)
        return result

    async def accept_convert_quote(self, quote_id: str) -> Dict:
        """Accept a convert quote to execute the swap"""
        params = {
            'quoteId': quote_id
        }
        result = await self._request('POST', '/sapi/v1/convert/acceptQuote', params)
        return result

    async def convert_asset(self, from_asset: str, to_asset: str, from_amount: float) -> Dict:
        """
        Convert/swap one asset to another using Binance Convert API.
        This works for small amounts without the spot market NOTIONAL minimum.
        Returns dict with keys: fromAsset, toAsset, fromAmount, toAmount, status
        """
        try:
            # Step 1: Get quote
            quote = await self.get_convert_quote(from_asset, to_asset, from_amount)

            if 'code' in quote:
                logger.error(f"Convert quote failed: {quote}")
                return quote

            quote_id = quote.get('quoteId')
            if not quote_id:
                logger.error(f"No quoteId in response: {quote}")
                return {'code': -1, 'msg': 'No quoteId in convert response'}

            to_amount = float(quote.get('toAmount', 0))
            logger.info(f"Convert quote: {from_amount} {from_asset} -> {to_amount} {to_asset}")

            # Step 2: Accept quote to execute
            result = await self.accept_convert_quote(quote_id)

            if 'code' in result:
                logger.error(f"Convert accept failed: {result}")
                return result

            # Success - return in similar format to market order
            return {
                'fromAsset': from_asset,
                'toAsset': to_asset,
                'fromAmount': from_amount,
                'toAmount': to_amount,
                'status': 'SUCCESS',
                'orderId': result.get('orderId', quote_id)
            }

        except Exception as e:
            logger.error(f"Convert {from_asset} to {to_asset} failed: {e}")
            return {'code': -1, 'msg': str(e)}

    # ==================== PRICE DATA (via python-binance) ====================

    async def get_price(self, symbol: str) -> float:
        """Get current price for a symbol"""
        try:
            ticker = await self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return 0.0


# Singleton instance
_client: Optional[BinanceClient] = None


async def get_client() -> BinanceClient:
    """Get or create the singleton client instance"""
    global _client
    if _client is None:
        _client = BinanceClient()
        await _client.initialize()
    return _client
