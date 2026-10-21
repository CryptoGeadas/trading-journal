"""Pydantic models for API schemas."""

from __future__ import annotations
from datetime import datetime
import re
from typing import Annotated, Optional

from fastapi import HTTPException
from pydantic import BaseModel, StringConstraints

# Colours end up in style attributes in the page, so only #rrggbb is allowed.
HEX_COLOUR_PATTERN = r"^#[0-9a-fA-F]{6}$"
HexColour = Annotated[str, StringConstraints(pattern=HEX_COLOUR_PATTERN)]
DEFAULT_COLOUR = "#6c9cfc"


def check_import_entries(entries, text_fields: tuple[str, ...], kind: str) -> list[dict]:
    """Validate every entry of an imported JSON file before anything is saved.

    Raises 400 naming the first bad entry, so a crafted or broken file adds nothing.
    Returns the entries with missing fields filled in.
    """
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail=f"The file has no list of {kind}.")
    clean = []
    for i, entry in enumerate(entries, 1):
        label = f"entry {i}"
        if not isinstance(entry, dict):
            raise HTTPException(status_code=400, detail=f"Import refused: {label} is not an object.")
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            label = f"'{name.strip()[:60]}'"
        for field in ("name",) + text_fields:
            value = entry.get(field)
            if value is not None and not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"Import refused: {field} of {label} must be text.")
        colour = entry.get("colour") or DEFAULT_COLOUR  # older exports may have none
        if not isinstance(colour, str) or not re.fullmatch(HEX_COLOUR_PATTERN, colour):
            raise HTTPException(status_code=400, detail=f"Import refused: colour of {label} must look like #34d399.")
        clean.append({**entry, "colour": colour})
    return clean


# --- Overview ---

class OverviewStats(BaseModel):
    total_trades: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    win_rate: float = 0.0
    connected_exchanges: int = 0
    last_sync: Optional[str] = None
    trades_this_month: int = 0
    trades_this_week: int = 0
    pnl_this_month: float = 0.0
    pnl_this_week: float = 0.0
    best_pair: Optional[str] = None
    worst_pair: Optional[str] = None


# --- Trades (individual fills) ---

class TradeOut(BaseModel):
    id: str
    exchange_id: str
    exchange: str
    external_id: str
    order_id: Optional[str] = None
    timestamp: str
    pair: str
    base_currency: str
    quote_currency: str
    side: str
    quantity: float
    price: float
    total: float
    fee: float
    fee_currency: str
    trade_type: str
    strategy: Optional[str] = None
    notes: Optional[str] = None
    emotion_tag: Optional[str] = None
    trade_rating: Optional[int] = None


class TradeUpdate(BaseModel):
    strategy: Optional[str] = None
    notes: Optional[str] = None
    emotion_tag: Optional[str] = None
    trade_rating: Optional[int] = None


class TradesPage(BaseModel):
    trades: list[TradeOut]
    total: int
    page: int
    page_size: int
    total_pages: int


# --- Orders (grouped fills) ---

class OrderOut(BaseModel):
    id: str
    group_id: str
    exchange_id: str
    exchange: str
    order_id: Optional[str] = None
    timestamp: str
    pair: str
    base_currency: str
    quote_currency: str
    side: str
    quantity: float
    price: float
    total: float
    fee: float
    fee_currency: str
    trade_type: str
    strategy: Optional[str] = None
    notes: Optional[str] = None
    emotion_tag: Optional[str] = None
    trade_rating: Optional[int] = None
    fill_count: int = 1


class OrdersPage(BaseModel):
    trades: list[OrderOut]
    total: int
    page: int
    page_size: int
    total_pages: int


# --- Exchanges ---

class ExchangeOut(BaseModel):
    id: str
    exchange: str
    label: Optional[str] = None
    is_read_only: bool = True
    last_sync_at: Optional[str] = None
    last_sync_status: Optional[str] = None
    trade_count: int = 0
    sync_start_date: Optional[str] = None
    created_at: str


class ExchangeCreate(BaseModel):
    exchange: str  # "binance" | "gateio" | "kraken"
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    label: Optional[str] = None
    sync_start_date: Optional[str] = None


# --- PnL ---

class PnlBreakdownItem(BaseModel):
    group: str
    trade_count: int
    total_pnl: float
    total_fees: float
    win_rate: float


class PnlResult(BaseModel):
    total_pnl: float
    total_fees: float
    trade_count: int
    method: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    breakdown: list[PnlBreakdownItem] = []


# --- Strategies ---

class StrategyOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    colour: str = "#6c9cfc"
    playbook: Optional[str] = None
    trade_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StrategyCreate(BaseModel):
    name: str
    description: Optional[str] = None
    colour: HexColour = DEFAULT_COLOUR
    playbook: Optional[str] = None


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    colour: Optional[HexColour] = None
    playbook: Optional[str] = None


# --- Emotion Tags ---

class EmotionTagOut(BaseModel):
    id: str
    name: str
    colour: str = "#6c9cfc"
    is_default: bool = False
    trade_count: int = 0
    created_at: Optional[str] = None


class EmotionTagCreate(BaseModel):
    name: str
    colour: HexColour = DEFAULT_COLOUR


class EmotionTagUpdate(BaseModel):
    name: Optional[str] = None
    colour: Optional[HexColour] = None


# --- Sync Log ---

class SyncLogEntry(BaseModel):
    id: int
    exchange_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: str
    trades_fetched: int
    error_message: Optional[str] = None
