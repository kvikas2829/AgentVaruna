from datetime import datetime, timezone


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes. Everything is stored as UTC, so tag them."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
