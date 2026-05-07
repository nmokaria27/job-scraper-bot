import asyncio
from datetime import datetime, timezone
import httpx
from scrapers.base import Job

# Discord limits 10 embeds per webhook call
MAX_EMBEDS_PER_POST = 10

# 0.5 seconds between webhook POSTs to stay under Discord's 30 req/min limit
RATE_LIMIT_SLEEP = 0.5

PLATFORM_LABELS: dict[str, str] = {
    "greenhouse": "\U0001f33f Greenhouse",
    "lever":      "\u2699\ufe0f Lever",
    "ashby":      "\U0001f537 Ashby",
    "simplify":   "\u26a1 SimplifyJobs",
    "hackernews": "\U0001f7e0 HN: Who's Hiring",
}

PLATFORM_COLORS: dict[str, int] = {
    "greenhouse": 0x3CB371,   # Green
    "lever":      0x4A90E2,   # Blue
    "ashby":      0x7B68EE,   # Purple
    "simplify":   0xFF6B35,   # Orange
    "hackernews": 0xFF6600,   # HN orange
}


def _default_webhook_url() -> str:
    import config

    return config.DISCORD_WEBHOOK_URL


async def _post_webhook(
    client: httpx.AsyncClient,
    payload: dict,
    webhook_url: str | None = None,
) -> bool:
    """POST a single webhook payload. Returns True on success."""
    url = webhook_url or _default_webhook_url()
    if not url:
        print("[ERROR] Discord webhook URL is missing")
        return False

    try:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return True
    except httpx.HTTPStatusError as e:
        print(f"[ERROR] Discord webhook HTTP error {e.response.status_code}: {e.response.text}")
        return False
    except httpx.RequestError as e:
        print(f"[ERROR] Discord webhook connection error: {e}")
        return False


def _build_job_embed(job: Job) -> dict:
    source_label = PLATFORM_LABELS.get(job.platform, job.platform.capitalize())
    color = PLATFORM_COLORS.get(job.platform, 5814783)
    return {
        "title": f"\U0001f680 {job.title}",
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
            success = await _post_webhook(client, payload, webhook_url)
            if success:
                notified.append(job)
            else:
                print(f"[ERROR] Failed to notify: {job.title} @ {job.company}")
            await asyncio.sleep(RATE_LIMIT_SLEEP)

    return notified


async def send_summary(
    new_count: int,
    total_checked: int,
    capped: bool = False,
    webhook_url: str | None = None,
    channel_name: str = "",
) -> None:
    """
    Send a summary embed at the end of a run.
    Only sends if new_count > 0.
    """
    if new_count == 0:
        return

    import config  # local import to avoid circular dependency at module level

    cap_note = (
        f"\n\u26a0\ufe0f Capped at {config.MAX_NOTIFICATIONS_PER_RUN} notifications. "
        "Additional matches were marked as seen and will not re-notify."
        if capped
        else ""
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
                "description": cap_note or "All new matches have been sent above.",
                "footer": {"text": "Job Scraper Bot"},
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        ]
    }

    async with httpx.AsyncClient(timeout=10) as client:
        await _post_webhook(client, payload, webhook_url)
