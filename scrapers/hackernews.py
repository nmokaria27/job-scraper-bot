import asyncio
import json
import re
from datetime import datetime, timezone
import httpx
from scrapers.base import BaseScraper, Job
import config

HN_BASE = "https://hacker-news.firebaseio.com/v0"
WHOISHIRING_USER_URL = f"{HN_BASE}/user/whoishiring.json"
ITEM_URL = f"{HN_BASE}/item/{{item_id}}.json"

# Only process this many top-level comments per thread (avoid rate limit abuse)
MAX_COMMENTS = 200

# Concurrency limit for fetching comments
SEMAPHORE_LIMIT = 10

# Pause between semaphore-throttled batches (polite to HN's free public API)
BATCH_SLEEP = 0.1


def _strip_html(html_text: str) -> str:
    """Strip HTML tags and decode common HTML entities."""
    if not html_text:
        return ""
    # Decode HTML entities before stripping tags
    text = html_text
    text = text.replace("&amp;", "&")
    text = text.replace("&#x27;", "'")
    text = text.replace("&#x2F;", "/")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&nbsp;", " ")
    # Replace <p> with newline so paragraphs become separate lines
    text = re.sub(r"<p>", "\n", text, flags=re.IGNORECASE)
    # Strip all remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _extract_title(raw_text: str) -> str:
    """
    Extract and clean the first meaningful line from a HN comment.
    HN 'Who is Hiring?' posts use the format:
      Company | Role | Location | ...
    """
    clean = _strip_html(raw_text)
    # Split on newlines, take first non-empty line
    for line in clean.splitlines():
        line = line.strip()
        if line:
            if len(line) > 120:
                return line[:120] + "..."
            return line
    return ""


def _extract_company(title: str) -> str:
    """First segment before ' | ', or 'HN Post' if no pipe found."""
    if " | " in title:
        return title.split(" | ")[0].strip()
    return "HN Post"


def _extract_location(title: str) -> str:
    """Third segment (index 2) after ' | ', or 'See posting' if not enough segments."""
    parts = title.split(" | ")
    if len(parts) >= 3:
        return parts[2].strip()
    return "See posting"


class HackerNewsScraper(BaseScraper):
    PLATFORM = "hackernews"

    async def _find_latest_thread(self, client: httpx.AsyncClient) -> int | None:
        """
        Fetch whoishiring user's submitted items and return the item ID of the
        most recent 'Ask HN: Who is hiring?' thread.
        """
        try:
            resp = await client.get(WHOISHIRING_USER_URL)
            resp.raise_for_status()
            user_data = resp.json()
        except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
            print(f"[ERROR] hackernews: failed to fetch whoishiring user — {e}")
            return None

        submitted = user_data.get("submitted", [])
        # Check only the first 5 submissions (newest first)
        for item_id in submitted[:5]:
            try:
                resp = await client.get(ITEM_URL.format(item_id=item_id))
                resp.raise_for_status()
                item = resp.json()
                title = (item.get("title") or "").lower()
                if title.startswith("ask hn: who is hiring?"):
                    return item_id
            except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError):
                continue

        print("[WARN] hackernews: could not find 'Who is Hiring?' thread in latest submissions")
        return None

    async def _fetch_comment(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        comment_id: int,
    ) -> dict | None:
        """Fetch a single HN comment under the semaphore. Returns raw item dict or None."""
        async with semaphore:
            try:
                resp = await client.get(ITEM_URL.format(item_id=comment_id))
                resp.raise_for_status()
                await asyncio.sleep(BATCH_SLEEP)
                return resp.json()
            except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError):
                return None

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        """
        Fetch the current month's HN 'Who is Hiring?' thread and return all
        top-level comments as Job objects. company_slug is ignored.
        """
        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            # Step 1: Find the latest thread
            thread_id = await self._find_latest_thread(client)
            if not thread_id:
                return []

            # Step 2: Get thread's top-level comment IDs
            try:
                resp = await client.get(ITEM_URL.format(item_id=thread_id))
                resp.raise_for_status()
                thread = resp.json()
            except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
                print(f"[ERROR] hackernews: failed to fetch thread {thread_id} — {e}")
                return []

            comment_ids = (thread.get("kids") or [])[:MAX_COMMENTS]
            if not comment_ids:
                print("[WARN] hackernews: thread has no top-level comments")
                return []

            print(f"[INFO] hackernews: fetching {len(comment_ids)} comments from thread {thread_id}")

            # Step 3: Fetch all comments concurrently with a semaphore
            semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
            raw_comments = await asyncio.gather(
                *[self._fetch_comment(client, semaphore, cid) for cid in comment_ids]
            )

        # Step 4: Parse into Job objects
        jobs: list[Job] = []
        for raw in raw_comments:
            if raw is None:
                continue
            if raw.get("deleted") or raw.get("dead"):
                continue

            raw_text = raw.get("text") or ""
            if not raw_text:
                continue

            title = _extract_title(raw_text)
            if not title:
                continue

            comment_id = raw["id"]
            company = _extract_company(title)
            location = _extract_location(title)

            # time is Unix timestamp in seconds
            time_val = raw.get("time")
            if time_val:
                try:
                    posted_at = datetime.fromtimestamp(time_val, tz=timezone.utc).isoformat()
                except (ValueError, OSError):
                    posted_at = "Unknown"
            else:
                posted_at = "Unknown"

            job = Job(
                id=f"hn-{comment_id}",
                title=title,
                company=company,
                location=location,
                url=f"https://news.ycombinator.com/item?id={comment_id}",
                platform=self.PLATFORM,
                posted_at=posted_at,
            )
            jobs.append(job)

        print(f"[OK] hackernews: {len(jobs)} job comments parsed")
        return jobs
