"""
Leverage Looper - Maximizes leverage to 4x on all Binance flexible loan positions
With P&L tracking to monitor profitability
"""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

from src.binance_client import BinanceClient
from src.leverage_looper import LeverageLooper
from src.profit_tracker import ProfitTracker

# Global state
client: BinanceClient = None
leverage_looper: LeverageLooper = None
profit_tracker: ProfitTracker = None
check_count = 0
last_result = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize on startup, cleanup on shutdown"""
    global client, leverage_looper, profit_tracker

    logger.info("Starting Leverage Looper...")

    client = BinanceClient()
    await client.initialize()
    leverage_looper = LeverageLooper(client)
    profit_tracker = ProfitTracker(client)

    # Record initial P&L snapshot
    try:
        initial_snapshot = await profit_tracker.record_snapshot()
        logger.info(f"Initial equity: ${initial_snapshot['total_equity_usd']:.2f}")
    except Exception as e:
        logger.error(f"Failed to record initial snapshot: {e}")

    # Start background monitoring
    task = asyncio.create_task(monitoring_loop())

    yield

    task.cancel()
    await client.close()
    logger.info("Shutdown complete")


app = FastAPI(title="Leverage Looper", lifespan=lifespan)


async def monitoring_loop():
    """Main monitoring loop - runs every 10 minutes"""
    global check_count, last_result

    while True:
        try:
            check_count += 1
            logger.info(f"=== Loop Check #{check_count} ===")

            # Run leverage loops to maximize leverage
            last_result = await leverage_looper.loop_all_positions()
            logger.info(f"Result: {last_result.get('positions_looped', 0)} positions looped, {last_result.get('total_loops', 0)} total loops")

            # Record P&L snapshot
            try:
                snapshot = await profit_tracker.record_snapshot()
                pnl = snapshot['pnl_absolute_usd']
                pnl_pct = snapshot['pnl_percent']
                equity = snapshot['total_equity_usd']
                sign = '+' if pnl >= 0 else ''
                logger.info(f"P&L: {sign}${pnl:.2f} ({sign}{pnl_pct:.2f}%) | Equity: ${equity:.2f}")
            except Exception as e:
                logger.warning(f"Failed to record P&L snapshot: {e}")

        except Exception as e:
            logger.error(f"Monitoring error: {e}")
            last_result = {"error": str(e)}

        await asyncio.sleep(600)  # 10 minutes


@app.get("/health")
async def health():
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "mode": "leverage_looper",
        "check_count": check_count,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@app.get("/status")
async def status():
    """Get current leverage status"""
    try:
        loans = await client.get_flexible_loan_ongoing_orders()

        leverage_info = []
        for loan in loans:
            lev = await leverage_looper.get_current_leverage(loan)
            leverage_info.append({
                "position": f"{loan.get('collateralCoin')}->{loan.get('loanCoin')}",
                "ltv": float(loan.get('currentLTV', 0)) * 100,
                "leverage": round(lev, 2)
            })

        return JSONResponse({
            "total_positions": len(loans),
            "positions": leverage_info,
            "check_count": check_count,
            "last_result": last_result
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/loop")
async def trigger_loop():
    """Manually trigger leverage looping on all positions"""
    global last_result
    try:
        last_result = await leverage_looper.loop_all_positions()
        return JSONResponse({"success": True, "result": last_result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/pnl")
async def get_pnl():
    """Get current P&L summary - ARE YOU MAKING MONEY?"""
    try:
        # Get fresh snapshot
        snapshot = await profit_tracker.record_snapshot()
        summary = profit_tracker.get_pnl_summary()

        pnl = snapshot['pnl_absolute_usd']
        pnl_pct = snapshot['pnl_percent']

        # Determine status
        if pnl > 0:
            status = "PROFIT"
            emoji = "profit"
        elif pnl < 0:
            status = "LOSS"
            emoji = "loss"
        else:
            status = "BREAKEVEN"
            emoji = "neutral"

        return JSONResponse({
            "status": status,
            "making_money": pnl > 0,
            "pnl_absolute_usd": round(pnl, 2),
            "pnl_percent": round(pnl_pct, 4),
            "current_equity_usd": round(snapshot['total_equity_usd'], 2),
            "starting_equity_usd": round(snapshot['starting_equity_usd'], 2),
            "starting_timestamp": snapshot['starting_timestamp'],
            "total_collateral_usd": round(snapshot['total_collateral_usd'], 2),
            "total_debt_usd": round(snapshot['total_debt_usd'], 2),
            "spot_value_usd": round(snapshot['spot_value_usd'], 2),
            "high_equity_usd": round(summary['high_equity'], 2) if summary['high_equity'] else None,
            "low_equity_usd": round(summary['low_equity'], 2) if summary['low_equity'] else None,
            "trend": summary['trend'],
            "snapshots_recorded": summary['snapshots_count'],
            "positions": snapshot['positions'],
            "timestamp": snapshot['timestamp']
        })
    except Exception as e:
        logger.error(f"P&L error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/pnl/history")
async def get_pnl_history(limit: int = 100):
    """Get historical P&L snapshots"""
    try:
        history = profit_tracker.get_history(limit)
        summary = profit_tracker.get_pnl_summary()
        return JSONResponse({
            "summary": summary,
            "history": history
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/pnl/reset")
async def reset_pnl():
    """Reset P&L tracking (start fresh from current equity)"""
    try:
        profit_tracker.reset_tracking()
        snapshot = await profit_tracker.record_snapshot()
        return JSONResponse({
            "success": True,
            "message": "P&L tracking reset",
            "new_starting_equity": round(snapshot['total_equity_usd'], 2)
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port)
