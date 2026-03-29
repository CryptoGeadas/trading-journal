"""Periodic sync scheduler using APScheduler."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import SYNC_INTERVAL_HOURS
from app.database import get_db
from app.exchanges.sync import sync_exchange

_scheduler = None


def start_scheduler():
    """Start the background scheduler for periodic trade syncing."""
    global _scheduler

    if _scheduler is not None:
        return  # Already running

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _sync_all_exchanges,
        trigger=IntervalTrigger(hours=SYNC_INTERVAL_HOURS),
        id="sync_all",
        name=f"Sync all exchanges every {SYNC_INTERVAL_HOURS}h",
        replace_existing=True,
    )
    _scheduler.start()
    print(f"[scheduler] Started — syncing every {SYNC_INTERVAL_HOURS} hours")


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        print("[scheduler] Stopped")


def _sync_all_exchanges():
    """Sync trades from all connected exchanges."""
    db = get_db()
    try:
        rows = db.execute("SELECT id FROM exchanges").fetchall()
    finally:
        db.close()

    if not rows:
        return

    print(f"[scheduler] Starting scheduled sync for {len(rows)} exchange(s)")

    for row in rows:
        try:
            result = sync_exchange(row["id"])
            print(f"[scheduler] {row['id']}: {result['status']} — {result['trades_fetched']} trades")
        except Exception as e:
            print(f"[scheduler] {row['id']}: error — {e}")
