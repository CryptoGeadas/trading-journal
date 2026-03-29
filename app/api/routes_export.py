"""Export endpoints — CSV/XLSX download. Phase 2 implementation."""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/export")
async def export_trades(
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
):
    """Export filtered trades as CSV or XLSX. Full implementation in Phase 2."""
    return JSONResponse(
        status_code=501,
        content={
            "status": "not_implemented",
            "message": "Export is Phase 2. This endpoint is scaffolded and ready.",
        },
    )
