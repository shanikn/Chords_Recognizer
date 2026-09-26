"""Times as people type and read them: "75", "1:15", "1:02:03" <-> seconds."""

from __future__ import annotations

import math

_FORMAT_HINT = "use SS, MM:SS or H:MM:SS"


def parse_time(text: str) -> float:
    """Parse "75", "1:15", "1:02:03" or "1:15.5" into seconds.

    Only the last field may have decimals. Minutes and seconds after the first field
    must be below 60. Raises ValueError otherwise.
    """
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or any(p == "" for p in parts):
        raise ValueError(f"invalid time {text!r}; {_FORMAT_HINT}")
    if any(not p.isdigit() for p in parts[:-1]):
        raise ValueError(f"invalid time {text!r}; {_FORMAT_HINT}")
    try:
        last = float(parts[-1])
    except ValueError:
        raise ValueError(f"invalid time {text!r}; {_FORMAT_HINT}") from None
    if not math.isfinite(last) or last < 0:
        raise ValueError(f"invalid time {text!r}; {_FORMAT_HINT}")
    values = [int(p) for p in parts[:-1]] + [last]
    # Every field after the first is minutes or seconds, so it must stay below 60.
    if any(v >= 60 for v in values[1:]):
        raise ValueError(f"invalid time {text!r}; minutes and seconds must be below 60")
    return float(sum(v * 60**i for i, v in enumerate(reversed(values))))


def format_time(seconds: float) -> str:
    """300 -> "5:00", 75.5 -> "1:15.5", 3723 -> "1:02:03"."""
    # Round the whole value to tenths first, so 59.96 carries over to "1:00".
    tenths_total = round(seconds * 10)
    whole, tenths = divmod(tenths_total, 10)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    tail = f"{secs:02d}" + (f".{tenths}" if tenths else "")
    return f"{hours}:{minutes:02d}:{tail}" if hours else f"{minutes}:{tail}"
