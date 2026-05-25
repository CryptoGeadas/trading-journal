"""Application configuration."""

import os

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Sync
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "6"))

# Display defaults
DEFAULT_QUOTE_CURRENCY = os.getenv("DEFAULT_QUOTE_CURRENCY", "USDT")
DEFAULT_PNL_METHOD = os.getenv("DEFAULT_PNL_METHOD", "fifo")  # "fifo" | "avg_cost"

# Authentication (optional — leave empty to disable)
AUTH_USER = os.getenv("AUTH_USER", "")
AUTH_PASS = os.getenv("AUTH_PASS", "")

# Auto-backup
BACKUP_RETENTION_COUNT = int(os.getenv("BACKUP_RETENTION_COUNT", "7"))
