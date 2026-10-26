"""Trading Journal — FastAPI Application."""

import base64
import binascii
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app import paths
from app.database import init_db
from app.encryption import KeyFileMissing
from app.services.pnl_engine import BadFilter
from app.scheduler import start_scheduler, stop_scheduler
from app.api.routes_overview import router as overview_router
from app.api.routes_trades import router as trades_router
from app.api.routes_exchanges import router as exchanges_router
from app.api.routes_pnl import router as pnl_router
from app.api.routes_export import router as export_router
from app.api.routes_strategies import router as strategies_router
from app.api.routes_screenshots import router as screenshots_router
from app.api.routes_backup import router as backup_router
from app.api.routes_funding import router as funding_router
from app.api.routes_patterns import router as patterns_router
from app.api.routes_notes import router as notes_router
from app.api.routes_emotion_tags import router as emotion_tags_router
from app.api.routes_demo import router as demo_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Move old in-repo data, then initialise database and scheduler on startup."""
    paths.migrate_legacy_data()
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Trading Journal",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Optional Basic Auth ---
from app.config import AUTH_USER, AUTH_PASS

if AUTH_USER and AUTH_PASS:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    class BasicAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/health":
                return await call_next(request)

            auth = request.headers.get("Authorization", "")
            if auth.startswith("Basic "):
                try:
                    user, password = base64.b64decode(auth[6:], validate=True).decode("utf-8").split(":", 1)
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    user, password = "", ""  # a broken header is just a wrong login
                # Constant-time compare: the time taken says nothing about the right values.
                user_ok = secrets.compare_digest(user.encode("utf-8"), AUTH_USER.encode("utf-8"))
                pass_ok = secrets.compare_digest(password.encode("utf-8"), AUTH_PASS.encode("utf-8"))
                if user_ok and pass_ok:
                    return await call_next(request)

            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Trading Journal"'},
            )

    app.add_middleware(BasicAuthMiddleware)

# --- Security headers ---
# The page loads only its own files. script-src 'self' blocks inline script and
# inline event handlers, so text that slips into the HTML can't run; and
# connect-src/img-src 'self' stop anything from sending data to another host.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
# FastAPI's own API docs pages load their scripts from a CDN.
_DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    if request.url.path not in _DOCS_PATHS:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# --- API routes ---
@app.exception_handler(KeyFileMissing)
async def key_file_missing(request: Request, exc: KeyFileMissing):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(BadFilter)
async def bad_filter(request: Request, exc: BadFilter):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(overview_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(exchanges_router, prefix="/api")
app.include_router(pnl_router, prefix="/api")
app.include_router(export_router, prefix="/api")
app.include_router(strategies_router, prefix="/api")
app.include_router(screenshots_router, prefix="/api")
app.include_router(backup_router, prefix="/api")
app.include_router(funding_router, prefix="/api")
app.include_router(patterns_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(emotion_tags_router, prefix="/api")
app.include_router(demo_router, prefix="/api")

# --- Static frontend ---
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if not FRONTEND_DIR.is_dir():
    raise RuntimeError(
        f"Frontend folder not found at {FRONTEND_DIR}. Run the journal from the repo folder "
        "with `python -m app` (install with `pip install -e .`, not `pip install .`)."
    )
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/system")
async def system_info():
    """Local-only details for the Settings page (behind auth, unlike /health)."""
    return {
        "data_dir": str(paths.data_dir()),
        "left_behind": [str(p) for p in paths.left_behind],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
