import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import httpx
from scrapers.base import Job

# Discord limits 10 embeds per webhook call
MAX_EMBEDS_PER_POST = 10

# 0.5 seconds between webhook POSTs to stay under Discord's 30 req/min limit
RATE_LIMIT_SLEEP = 0.5

PLATFORM_LABELS: dict[str, str] = {
    "greenhouse":  "\U0001f33f Greenhouse",
    "lever":       "\u2699\ufe0f Lever",
    "ashby":       "\U0001f537 Ashby",
    "amazon":      "\U0001f4e6 Amazon Jobs",
    "workday":     "\U0001f3e2 Workday",
    "simplify":    "\u26a1 SimplifyJobs",
    "speedyapply": "\U0001f4a8 SpeedyApply",
    "jobright":    "\U0001f3af JobRight",
    "zapply":      "\U0001f680 Zapply",
    "applyguy":    "\U0001f9ed ApplyGuy",
    "gradtracker": "\U0001f4cb New-Grad Tracker",
    "hackernews":  "\U0001f7e0 HN: Who's Hiring",
}

PLATFORM_COLORS: dict[str, int] = {
    "greenhouse":  0x3CB371,   # Green
    "lever":       0x4A90E2,   # Blue
    "ashby":       0x7B68EE,   # Purple
    "amazon":      0xFF9900,   # Amazon orange
    "workday":     0xF38B00,   # Workday orange
    "simplify":    0xFF6B35,   # Orange
    "speedyapply": 0x1ABC9C,   # Teal
    "jobright":    0xEB459E,   # Pink
    "zapply":      0x2ECC71,   # Bright green
    "applyguy":    0x9B59B6,   # Violet
    "gradtracker": 0x95A5A6,   # Grey
    "hackernews":  0xFF6600,   # HN orange
}

# Discord returns 429 with a retry_after (seconds) when a webhook is posted to
# too quickly. Honour it instead of dropping the notification.
MAX_RATE_LIMIT_RETRIES = 3


FATAL_WEBHOOK_STATUS_CODES = {401, 404, 405}


@dataclass
class WebhookPostResult:
    success: bool
    fatal: bool = False


def _default_webhook_url() -> str:
    import config

    return config.DISCORD_WEBHOOK_URL


async def _post_webhook(
    client: httpx.AsyncClient,
    payload: dict,
    webhook_url: str | None = None,
) -> WebhookPostResult:
    """POST a single webhook payload."""
    url = webhook_url or _default_webhook_url()
    if not url:
        print("[ERROR] Discord webhook URL is missing")
        return WebhookPostResult(success=False, fatal=True)

    for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 2):
        try:
            response = await client.post(url, json=payload)
        except httpx.RequestError as e:
            print(f"[ERROR] Discord webhook connection error: {type(e).__name__}: {e}")
            return WebhookPostResult(success=False)

        if response.status_code == 429 and attempt <= MAX_RATE_LIMIT_RETRIES:
            retry_after = _retry_after_seconds(response)
            print(f"[WARN] Discord rate limited (429); retrying in {retry_after:.1f}s")
            await asyncio.sleep(retry_after)
            continue

        if response.is_success:
            return WebhookPostResult(success=True)

        print(f"[ERROR] Discord webhook HTTP error {response.status_code}: {response.text[:200]}")
        return WebhookPostResult(
            success=False,
            fatal=response.status_code in FATAL_WEBHOOK_STATUS_CODES,
        )

    return WebhookPostResult(success=False)


def _retry_after_seconds(response: httpx.Response) -> float:
    """Discord sends retry_after in the JSON body (seconds) and/or a header."""
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("retry_after") is not None:
            return min(float(body["retry_after"]) + 0.25, 30.0)
    except (ValueError, TypeError):
        pass
    header = response.headers.get("Retry-After") or response.headers.get("X-RateLimit-Reset-After")
    try:
        return min(float(header) + 0.25, 30.0) if header else 2.0
    except ValueError:
        return 2.0


def _build_job_embed(job: Job) -> dict:
    source_label = PLATFORM_LABELS.get(job.platform, job.platform.capitalize())
    color = PLATFORM_COLORS.get(job.platform, 5814783)
    full_title = f"\U0001f680 {job.title}"
    title = (full_title[:253] + "...") if len(full_title) > 256 else full_title
    return {
        "title": title,
        "description": f"**{job.company}**",
        "color": color,
        "fields": [
            {
                "name": "\U0001f4e1 Source",
                "value": source_label,
                "inline": True,
            },
            {
                "name": "\U0001f4cd Location",
                "value": job.location or "Remote / Not Specified",
                "inline": True,
            },
            {
                "name": "\U0001f550 Posted",
                "value": job.posted_at,
                "inline": True,
            },
            {
                "name": "\U0001f517 Apply",
                "value": f"[Click Here]({job.url})" if job.url else "No link available",
                "inline": False,
            },
        ],
        "footer": {"text": "Job Scraper Bot"},
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


async def notify_jobs_batch(jobs: list[Job], webhook_url: str | None = None) -> list[Job]:
    """
    Send Discord notifications for a list of jobs to a specific webhook.
    Returns the subset of jobs that were successfully notified.
    Rate-limits between each POST.
    """
    notified: list[Job] = []

    async with httpx.AsyncClient(timeout=10) as client:
        for job in jobs:
            payload = {"embeds": [_build_job_embed(job)]}
            result = await _post_webhook(client, payload, webhook_url)
            if result.success:
                notified.append(job)
            else:
                print(f"[ERROR] Failed to notify: {job.title} @ {job.company}")
                if result.fatal:
                    print("[ERROR] Stopping channel notifications because the webhook endpoint is invalid")
                    break
            await asyncio.sleep(RATE_LIMIT_SLEEP)

    return notified


async def send_summary(
    new_count: int,
    total_checked: int,
    capped: bool = False,
    webhook_url: str | None = None,
    channel_name: str = "",
    force: bool = False,
) -> None:
    """
    Send a summary embed at the end of a run.
    Only sends if new_count > 0 unless force=True.
    """
    if new_count == 0 and not force:
        return

    import config  # local import to avoid circular dependency at module level

    cap_note = (
        f"\n\u26a0\ufe0f Capped at {config.MAX_NOTIFICATIONS_PER_RUN} notifications. "
        "Additional matches were marked as seen and will not re-notify."
        if capped
        else ""
    )
    description = (
        "No new matching jobs were found in this run."
        if new_count == 0
        else cap_note or "All new matches have been sent above."
    )

    title = (
        f"\U0001f4ca Job Scraper \u2014 {channel_name}"
        if channel_name
        else "\U0001f4ca Job Scraper Run Complete"
    )

    payload = {
        "embeds": [
            {
                "title": title,
                "color": 3066993,  # Green
                "fields": [
                    {
                        "name": "New jobs found",
                        "value": str(new_count),
                        "inline": True,
                    },
                    {
                        "name": "Total postings checked",
                        "value": str(total_checked),
                        "inline": True,
                    },
                ],
                "description": description,
                "footer": {"text": "Job Scraper Bot"},
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        ]
    }

    async with httpx.AsyncClient(timeout=10) as client:
        await _post_webhook(client, payload, webhook_url)
