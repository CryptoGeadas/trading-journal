"""Trading Journal — FastAPI Application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.api.routes_overview import router as overview_router
from app.api.routes_trades import router as trades_router
from app.api.routes_exchanges import router as exchanges_router
from app.api.routes_pnl import router as pnl_router
from app.api.routes_export import router as export_router
from app.api.routes_strategies import router as strategies_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise database and scheduler on startup."""
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Trading Journal",
    version="0.1.0",
    lifespan=lifespan,
)

# --- API routes ---
app.include_router(overview_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(exchanges_router, prefix="/api")
app.include_router(pnl_router, prefix="/api")
app.include_router(export_router, prefix="/api")
app.include_router(strategies_router, prefix="/api")

# --- Static frontend ---
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
