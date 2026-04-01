"""Export endpoints — CSV and XLSX download of filtered trades."""

import io
import csv
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from app.database import get_db

router = APIRouter()

EXPORT_COLUMNS = [
    ("timestamp", "Time"),
    ("exchange", "Exchange"),
    ("pair", "Pair"),
    ("side", "Side"),
    ("quantity", "Quantity"),
    ("price", "Avg Price"),
    ("total", "Total"),
    ("fee", "Fee"),
    ("fee_currency", "Fee Currency"),
    ("fill_count", "Fills"),
    ("order_id", "Order ID"),
    ("strategy", "Strategy"),
    ("notes", "Notes"),
]


def _query_grouped_trades(
    exchange: Optional[str],
    pair: Optional[str],
    side: Optional[str],
    strategy: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    order_by: str,
    order_dir: str,
) -> list[dict]:
    """Query trades grouped by order, applying filters."""
    db = get_db()
    try:
        conditions = []
        params = []

        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange)
        if pair:
            conditions.append("pair = ?")
            params.append(pair)
        if side:
            conditions.append("side = ?")
            params.append(side)
        if strategy:
            if strategy == "__untagged__":
                conditions.append("strategy IS NULL")
            else:
                conditions.append("strategy = ?")
                params.append(strategy)
        if date_from:
            conditions.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("timestamp <= ?")
            params.append(date_to)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        group_key = "COALESCE(order_id, id)"

        query = f"""
            SELECT
                MIN(timestamp)       as timestamp,
                exchange,
                pair,
                side,
                SUM(quantity)        as quantity,
                CASE WHEN SUM(quantity) > 0
                     THEN SUM(total) / SUM(quantity)
                     ELSE 0 END      as price,
                SUM(total)           as total,
                SUM(fee)             as fee,
                MIN(fee_currency)    as fee_currency,
                COUNT(*)             as fill_count,
                order_id,
                MIN(strategy)        as strategy,
                MIN(notes)           as notes
            FROM trades {where}
            GROUP BY {group_key}, exchange_id, pair, side
            ORDER BY {order_by} {order_dir}
        """
        rows = db.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.get("/export")
async def export_trades(
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    side: Optional[str] = None,
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    order_by: str = Query("timestamp", pattern="^(timestamp|pair|exchange|total)$"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
):
    """Export filtered trades as CSV or XLSX."""
    rows = _query_grouped_trades(
        exchange, pair, side, strategy, date_from, date_to, order_by, order_dir
    )

    # Generate filename with timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"trades_export_{ts}"

    if format == "csv":
        return _export_csv(rows, filename)
    else:
        return _export_xlsx(rows, filename)


def _export_csv(rows: list[dict], filename: str) -> StreamingResponse:
    """Generate CSV file as streaming response."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([col[1] for col in EXPORT_COLUMNS])

    # Data
    for row in rows:
        writer.writerow([
            _format_value(row.get(col[0], ""), col[0])
            for col in EXPORT_COLUMNS
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
    )


def _export_xlsx(rows: list[dict], filename: str) -> StreamingResponse:
    """Generate XLSX file as streaming response."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Trades"

    # Styles
    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(start_color="1C1D24", end_color="1C1D24", fill_type="solid")
    header_text = Font(bold=True, size=11, color="E8E9ED")
    number_fmt_2 = '0.00'
    number_fmt_8 = '0.00000000'

    # Header row
    headers = [col[1] for col in EXPORT_COLUMNS]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_text
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    # Data rows
    for row_idx, row in enumerate(rows, 2):
        for col_idx, (key, _) in enumerate(EXPORT_COLUMNS, 1):
            value = row.get(key, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=_format_value(value, key))

            # Number formatting
            if key in ("total", "fee"):
                try:
                    cell.value = round(float(value), 2)
                    cell.number_format = number_fmt_2
                except (ValueError, TypeError):
                    pass
            elif key == "quantity":
                try:
                    cell.value = float(value)
                    cell.number_format = number_fmt_8
                except (ValueError, TypeError):
                    pass
            elif key == "price":
                try:
                    cell.value = round(float(value), 8)
                    cell.number_format = number_fmt_8
                except (ValueError, TypeError):
                    pass

            # Side colouring
            if key == "side" and value:
                if value.lower() == "buy":
                    cell.font = Font(color="34D399")
                elif value.lower() == "sell":
                    cell.font = Font(color="F87171")

    # Auto-width columns
    for col_idx in range(1, len(headers) + 1):
        max_len = len(str(headers[col_idx - 1]))
        for row_idx in range(2, len(rows) + 2):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 3, 30)

    # Freeze header row
    ws.freeze_panes = "A2"

    # Write to bytes
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}.xlsx"},
    )


def _format_value(value, key: str):
    """Format a value for export."""
    if value is None:
        return ""
    if key == "timestamp" and value:
        # Clean up ISO format for readability
        return str(value).replace("T", " ").replace("+00:00", " UTC")
    if key == "side" and value:
        return value.upper()
    if key == "exchange" and value:
        return value.capitalize()
    return value
