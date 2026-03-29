"""Pydantic models for API schemas."""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


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


# --- Trades ---

class TradeOut(BaseModel):
    id: str
    exchange_id: str
    exchange: str
    external_id: str
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


class TradeUpdate(BaseModel):
    strategy: Optional[str] = None
    notes: Optional[str] = None


class TradesPage(BaseModel):
    trades: list[TradeOut]
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
    created_at: str


class ExchangeCreate(BaseModel):
    exchange: str  # "binance" | "bybit" | "gateio"
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    label: Optional[str] = None


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
    strategy: str
    trade_count: int


# --- Sync Log ---

class SyncLogEntry(BaseModel):
    id: int
    exchange_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: str
    trades_fetched: int
    error_message: Optional[str] = None
