"""Shared utility functions used across scrapers and orchestration."""

import json
from datetime import datetime, timezone


def timestamp_to_iso(value: int | float | None, unit: str = "seconds") -> str:
    """
    Convert a Unix timestamp to an ISO 8601 string (UTC).

    Args:
        value: Unix timestamp (int or float), or None.
        unit: "seconds" or "milliseconds" — divides by 1000 when milliseconds.

    Returns:
        ISO string on success, "Unknown" on failure or if value is falsy.
    """
    if not value:
        return "Unknown"
    try:
        divisor = 1000 if unit == "milliseconds" else 1
        return datetime.fromtimestamp(value / divisor, tz=timezone.utc).isoformat()
    except (ValueError, OSError, TypeError):
        return "Unknown"


def format_company_name(slug: str) -> str:
    """Convert an ATS company slug to a display-friendly name.

    Examples:
        "stripe" → "Stripe"
        "jane-street" → "Jane Street"
    """
    return slug.replace("-", " ").title()


def load_json_file(path: str, defaults: dict) -> dict:
    """
    Load a JSON file and return its contents as a dict.

    Handles missing files and corrupt JSON gracefully, returning `defaults`
    in those cases. Required keys from `defaults` are filled in via setdefault.
    """
    try:
        with open(path, "r") as f:
            data = json.load(f)
        for key, value in defaults.items():
            data.setdefault(key, value)
        return data
    except FileNotFoundError:
        print(f"[INFO] {path} not found — starting fresh.")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[WARN] {path} is corrupt ({e}) — starting fresh.")
    return dict(defaults)


def save_json_file(path: str, data: dict) -> None:
    """Write a dict to a JSON file with 2-space indentation."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
