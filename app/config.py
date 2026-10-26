"""Application configuration.

Settings come from environment variables. A `.env` file in the repo folder
is read first; a real environment variable with the same name wins.
"""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path = ENV_FILE) -> list[str]:
    """Put KEY=VALUE lines of `path` into os.environ unless already set.

    Returns the names it set. Blank lines and # comments are skipped; quotes
    around a value are removed. No extra package needed.
    """
    if not path.is_file():
        return []
    loaded = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.replace("_", "").isalnum():
            print(f"[config] .env line {number} is not KEY=VALUE; ignored")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


load_env_file()

# Server — 127.0.0.1 means only this computer can open the journal
HOST = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("PORT", "8000"))

# Sync
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "6"))

# Display defaults
DEFAULT_PNL_METHOD = os.getenv("DEFAULT_PNL_METHOD", "fifo")  # "fifo" | "avg_cost"

# Authentication (optional — leave empty to disable)
AUTH_USER = os.getenv("AUTH_USER", "")
AUTH_PASS = os.getenv("AUTH_PASS", "")

# Auto-backup
BACKUP_RETENTION_COUNT = int(os.getenv("BACKUP_RETENTION_COUNT", "7"))
