"""
Discover ATS board slugs from discovery sources and validate them.

Outputs a JSON file that can be merged by companies.get_companies().
"""

import argparse
import asyncio
import json
import os
import re
from collections.abc import Iterable

import httpx

OUTPUT_PATH_DEFAULT = "discovered_companies.json"
DISCOVERY_TIMEOUT = 15
VALIDATION_TIMEOUT = 8
MAX_CONCURRENCY = 20

GREENHOUSE_PATTERN = re.compile(
    r"https?://(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
GREENHOUSE_API_PATTERN = re.compile(
    r"https?://boards-api\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)/jobs",
    re.IGNORECASE,
)
LEVER_PATTERN = re.compile(
    r"https?://jobs\.lever\.co/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
ASHBY_PATTERN = re.compile(
    r"https?://jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

VALIDATION_URLS: dict[str, str] = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


def _parse_sources(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _iter_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)


def _extract_candidates_from_text(text: str) -> dict[str, set[str]]:
    candidates = {
        "greenhouse": set(GREENHOUSE_PATTERN.findall(text)),
        "lever": set(LEVER_PATTERN.findall(text)),
        "ashby": set(ASHBY_PATTERN.findall(text)),
    }
    candidates["greenhouse"].update(GREENHOUSE_API_PATTERN.findall(text))
    return candidates


def _merge_candidates(base: dict[str, set[str]], extra: dict[str, set[str]]) -> None:
    for platform in ("greenhouse", "lever", "ashby"):
        base[platform].update(extra.get(platform, set()))


async def _fetch_source(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text


async def collect_candidates(source_urls: list[str]) -> dict[str, set[str]]:
    candidates: dict[str, set[str]] = {
        "greenhouse": set(),
        "lever": set(),
        "ashby": set(),
    }
    if not source_urls:
        return candidates

    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT, follow_redirects=True) as client:
        for url in source_urls:
            try:
                text = await _fetch_source(client, url)
            except httpx.HTTPError as e:
                print(f"[WARN] discovery source failed ({url}): {e}")
                continue

            _merge_candidates(candidates, _extract_candidates_from_text(text))
            try:
                payload = json.loads(text)
                for chunk in _iter_strings(payload):
                    _merge_candidates(candidates, _extract_candidates_from_text(chunk))
            except json.JSONDecodeError:
                pass

    return candidates


async def _validate_slug(
    client: httpx.AsyncClient,
    platform: str,
    slug: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str, bool]:
    async with semaphore:
        url = VALIDATION_URLS[platform].format(slug=slug)
        try:
            response = await client.get(url)
            if response.status_code == 404:
                return platform, slug, False
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return platform, slug, False

        if platform == "greenhouse":
            return platform, slug, isinstance(payload, dict) and "jobs" in payload
        if platform == "lever":
            return platform, slug, isinstance(payload, list)
        if platform == "ashby":
            return platform, slug, isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
        return platform, slug, False


async def validate_candidates(candidates: dict[str, set[str]]) -> dict[str, list[str]]:
    validated: dict[str, list[str]] = {"greenhouse": [], "lever": [], "ashby": []}
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with httpx.AsyncClient(timeout=VALIDATION_TIMEOUT, follow_redirects=True) as client:
        tasks = [
            _validate_slug(client, platform, slug, semaphore)
            for platform in ("greenhouse", "lever", "ashby")
            for slug in sorted(candidates.get(platform, set()))
        ]
        if not tasks:
            return validated

        for platform, slug, ok in await asyncio.gather(*tasks):
            if ok:
                validated[platform].append(slug)

    for platform in validated:
        validated[platform] = sorted(set(validated[platform]))
    return validated


def _load_existing(path: str) -> dict[str, list[str]]:
    try:
        with open(path, "r") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return {"greenhouse": [], "lever": [], "ashby": []}
    except (json.JSONDecodeError, OSError):
        return {"greenhouse": [], "lever": [], "ashby": []}

    if not isinstance(payload, dict):
        return {"greenhouse": [], "lever": [], "ashby": []}
    return {
        platform: [str(x).strip() for x in payload.get(platform, []) if str(x).strip()]
        for platform in ("greenhouse", "lever", "ashby")
    }


def _merge_existing(existing: dict[str, list[str]], found: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for platform in ("greenhouse", "lever", "ashby"):
        merged[platform] = sorted(set(existing.get(platform, []) + found.get(platform, [])))
    return merged


def _print_summary(title: str, data: dict[str, list[str]]) -> None:
    print(f"\n{title}")
    for platform in ("greenhouse", "lever", "ashby"):
        print(f"  - {platform}: {len(data.get(platform, []))}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Discover and validate ATS company slugs")
    parser.add_argument(
        "--sources",
        default=os.getenv("DISCOVERY_SOURCE_URLS", ""),
        help="Comma-separated URLs to scan for ATS links",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("DISCOVERED_COMPANIES_PATH", OUTPUT_PATH_DEFAULT),
        help="Output JSON path for discovered companies",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace output file instead of merging with existing slugs",
    )
    args = parser.parse_args()

    source_urls = _parse_sources(args.sources)
    if not source_urls:
        print("[WARN] No discovery sources configured (set --sources or DISCOVERY_SOURCE_URLS)")
        return

    print(f"[INFO] Scanning {len(source_urls)} discovery source(s)")
    candidates = await collect_candidates(source_urls)
    print(
        "[INFO] Candidate slugs:"
        f" greenhouse={len(candidates['greenhouse'])},"
        f" lever={len(candidates['lever'])},"
        f" ashby={len(candidates['ashby'])}"
    )

    validated = await validate_candidates(candidates)
    _print_summary("[INFO] Validated new slugs", validated)

    if args.replace:
        output = validated
    else:
        existing = _load_existing(args.output)
        output = _merge_existing(existing, validated)
        _print_summary("[INFO] Merged discovered slugs", output)

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    print(f"[OK] Wrote discovered slugs to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
