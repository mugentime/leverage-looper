"""
Leverage Looper - Maximizes leverage to 4x on all Binance flexible loan positions
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

# Global state
client: BinanceClient = None
leverage_looper: LeverageLooper = None
check_count = 0
last_result = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize on startup, cleanup on shutdown"""
    global client, leverage_looper

    logger.info("Starting Leverage Looper...")

    client = BinanceClient()
    await client.initialize()
    leverage_looper = LeverageLooper(client)

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

            # Run leverage loops to maximize leverage to 4x
            last_result = await leverage_looper.loop_all_positions()
            logger.info(f"Result: {last_result.get('positions_looped', 0)} positions looped, {last_result.get('total_loops', 0)} total loops")

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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port)
