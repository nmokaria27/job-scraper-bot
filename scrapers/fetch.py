"""
Shared HTTP helpers for all scrapers.

Every scraper used to build its own httpx client with no retries and a 10s
timeout, and printed empty error messages for timeouts (``str(ReadTimeout())``
is ""). Large boards (Databricks ~900 jobs, Figma) were timing out on every
GitHub Actions run. This module centralises:

- one client factory (redirects on, gzip only — avoids a brotli decoder bug)
- retry with backoff on timeouts / connection errors / retryable status codes
- error messages that always include the exception type
"""

import asyncio
import json

import httpx

import config

USER_AGENT = "job-scraper-bot/1.0 (+https://github.com/nmokaria27/job-scraper-bot)"

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    # Explicitly exclude brotli: the optional `brotli` package makes httpx
    # decode `br` responses and it crashes on some large Ashby payloads.
    "Accept-Encoding": "gzip, deflate",
}

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def make_client(
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """Create an AsyncClient with the project-wide defaults."""
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    return httpx.AsyncClient(
        timeout=timeout if timeout is not None else config.REQUEST_TIMEOUT,
        follow_redirects=True,
        headers=merged_headers,
    )


def describe_error(exc: BaseException) -> str:
    """Human-readable error text that never collapses to an empty string."""
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


async def fetch_response(
    client: httpx.AsyncClient,
    url: str,
    label: str,
    method: str = "GET",
    json_body: object | None = None,
) -> httpx.Response | None:
    """
    Request ``url`` with retries. Returns a successful response, or None after
    logging the failure. 404s are logged as a warning (board/repo moved) and
    are not retried.
    """
    max_attempts = max(1, config.REQUEST_RETRY_ATTEMPTS + 1)

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.request(method, url, json=json_body)
        except httpx.RequestError as e:
            if attempt < max_attempts:
                print(
                    f"[WARN] {label}: {describe_error(e)} on attempt "
                    f"{attempt}/{max_attempts}; retrying"
                )
                await asyncio.sleep(attempt)
                continue
            print(f"[ERROR] {label}: request failed — {describe_error(e)}")
            return None

        if response.status_code == 404:
            print(f"[WARN] {label}: not found (404) — source moved or slug is wrong")
            return None

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_attempts:
            print(
                f"[WARN] {label}: HTTP {response.status_code} on attempt "
                f"{attempt}/{max_attempts}; retrying"
            )
            await asyncio.sleep(attempt)
            continue

        if response.is_success:
            return response

        print(f"[ERROR] {label}: HTTP {response.status_code}")
        return None

    return None


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    label: str,
    method: str = "GET",
    json_body: object | None = None,
) -> object | None:
    """Request + JSON decode with retries. Returns None on any failure."""
    response = await fetch_response(client, url, label, method=method, json_body=json_body)
    if response is None:
        return None
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[ERROR] {label}: failed to parse JSON — {describe_error(e)}")
        return None


async def fetch_text(client: httpx.AsyncClient, url: str, label: str) -> str | None:
    """GET + text body with retries. Returns None on any failure."""
    response = await fetch_response(client, url, label)
    if response is None:
        return None
    return response.text
