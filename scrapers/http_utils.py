"""Shared HTTP fetch utilities for scrapers."""

import json

import httpx


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    platform: str,
    slug: str,
) -> tuple[object | None, bool]:
    """
    GET a URL and parse its JSON response, handling common error cases.

    Returns (parsed_data, is_404).  On success: (data, False).
    On 404: (None, True).  On other errors: (None, False) after logging.
    """
    try:
        response = await client.get(url)

        if response.status_code == 404:
            print(f"[WARN] {platform}/{slug}: company not found on {platform.title()} (404)")
            return None, True

        response.raise_for_status()
        data = response.json()
        return data, False

    except httpx.HTTPStatusError as e:
        print(f"[ERROR] {platform}/{slug}: HTTP error {e.response.status_code} — {e}")
        return None, False
    except httpx.RequestError as e:
        print(f"[ERROR] {platform}/{slug}: connection error — {e}")
        return None, False
    except json.JSONDecodeError as e:
        print(f"[ERROR] {platform}/{slug}: failed to parse JSON — {e}")
        return None, False
