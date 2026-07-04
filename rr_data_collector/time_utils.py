from __future__ import annotations

from datetime import datetime, timezone


def utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def parse_time_ms(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if len(normalized) == 10:
        normalized = normalized + "T00:00:00+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def iso_from_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return (
        datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
