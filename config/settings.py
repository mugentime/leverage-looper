"""Simple configuration for Leverage Looper"""
import os


class Settings:
    """Configuration settings"""
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
    PORT = int(os.getenv("PORT", "8003"))
    TARGET_LTV = 0.75
    MAX_LEVERAGE = 3.9  # Stop at 3.9x for safety (theoretical max is 4x)
    MIN_LOOP_USD = 1.0  # Minimum $1 per loop


settings = Settings()

# Also export as module-level for backwards compat
BINANCE_API_KEY = settings.BINANCE_API_KEY
BINANCE_API_SECRET = settings.BINANCE_API_SECRET
PORT = settings.PORT
TARGET_LTV = settings.TARGET_LTV
MAX_LEVERAGE = settings.MAX_LEVERAGE
MIN_LOOP_USD = settings.MIN_LOOP_USD
