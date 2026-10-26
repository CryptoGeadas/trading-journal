"""One timestamp format for everything stored: UTC, fixed width, `Z`.

Matching, filters and month/week grouping compare timestamps as text, so
every stored time must be the same format: `2026-01-31T23:00:00.000Z`.
A time typed with an offset (`+02:00`) is turned into the real UTC moment.
A time with no offset is taken as UTC.
"""

from datetime import date, datetime, timedelta, timezone

FORMAT = "%Y-%m-%dT%H:%M:%S"


def format_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return f"{dt.strftime(FORMAT)}.{dt.microsecond // 1000:03d}Z"


def to_utc(value: str) -> str:
    """Normalise an ISO time. Raises ValueError for text that is not a time."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    return format_utc(datetime.fromisoformat(text))


def from_ms(ms: int) -> str:
    return format_utc(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))


def now_utc() -> str:
    return format_utc(datetime.now(timezone.utc))


def is_date_only(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return len(value) == 10
    except ValueError:
        return False


def filter_bounds(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    """(inclusive start, exclusive end) as stored-format text.

    A date-only `date_to` means the whole of that day, so the end is the
    start of the next day.
    """
    start = end = None
    if date_from:
        start = to_utc(date_from) if not is_date_only(date_from) else f"{date_from}T00:00:00.000Z"
    if date_to:
        if is_date_only(date_to):
            end = f"{date.fromisoformat(date_to) + timedelta(days=1)}T00:00:00.000Z"
        else:
            end = format_utc(datetime.fromisoformat(to_utc(date_to)[:-1] + "+00:00") + timedelta(milliseconds=1))
    return start, end
