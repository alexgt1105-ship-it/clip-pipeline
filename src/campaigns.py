from __future__ import annotations

import json
import re
import time

import yt_dlp

from config import WATCHLIST_PATH, YTDLP_AUTH

DEFAULTS = {
    "status": "queued",
    "sources": [],
    "clips_target": 0,        # 0 = no cap; run until the sources run dry
    "max_clips_per_video": 5,
    "layout": None,           # None = fall back to DEFAULT_LAYOUT
    "notes": "",
}


def load() -> dict:
    if not WATCHLIST_PATH.exists():
        return {"campaigns": []}
    return json.loads(WATCHLIST_PATH.read_text())


def save(data: dict) -> None:
    tmp = WATCHLIST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(WATCHLIST_PATH)


def add(campaign_id: str, sources: list[str], **fields) -> dict:
    """Register a new contract. The first one added becomes active immediately; later ones
    queue behind it and start on their own when the one ahead finishes."""
    data = load()
    if any(c["id"] == campaign_id for c in data["campaigns"]):
        raise ValueError(f"campaign {campaign_id!r} already exists")

    campaign = dict(DEFAULTS)
    campaign.update(fields)
    campaign.update({"id": campaign_id, "sources": sources, "added_at": time.time()})

    if not any(c["status"] == "active" for c in data["campaigns"]):
        campaign["status"] = "active"

    data["campaigns"].append(campaign)
    save(data)
    return campaign


def active(data: dict | None = None) -> dict | None:
    data = data or load()
    for c in data["campaigns"]:
        if c["status"] == "active":
            return c
    return None


def finish(campaign_id: str, reason: str) -> dict | None:
    """Close out a contract and promote the next queued one.

    This is what makes the pipeline survive a contract ending without you touching it:
    the runner calls this the moment a campaign hits its target or runs out of source
    video, and the next contract in the queue is active before the next scheduled run.
    """
    data = load()
    promoted = None
    for c in data["campaigns"]:
        if c["id"] == campaign_id:
            c["status"] = "done"
            c["finished_at"] = time.time()
            c["finished_reason"] = reason
    for c in data["campaigns"]:
        if c["status"] == "queued":
            c["status"] = "active"
            promoted = c
            break
    save(data)
    return promoted


# A bare channel URL resolves to a set of tabs (Videos / Live / Shorts) that carry no
# URL of their own, so it has to be pointed at the tabs we actually want. Shorts are
# skipped deliberately — re-clipping someone else's Shorts is not the job.
CHANNEL_ROOT_RE = re.compile(
    r"^https?://(www\.)?youtube\.com/(@[^/?#]+|c/[^/?#]+|channel/[^/?#]+|user/[^/?#]+)/?$"
)
CHANNEL_TABS = ["/videos", "/streams"]

# Anything shorter than this is not a clippable source — it is already a short.
MIN_SOURCE_SECONDS = 180


def _expand_channel(url: str) -> list[str]:
    if CHANNEL_ROOT_RE.match(url):
        base = url.rstrip("/")
        return [base + tab for tab in CHANNEL_TABS]
    return [url]


def _list_one(url: str, limit: int) -> list[dict]:
    opts = {
        # extract_flat lists entries from the index page instead of resolving every
        # video, which is the difference between one request and a hundred.
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        **YTDLP_AUTH,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - an empty tab (no livestreams) is normal
        print(f"[campaigns] {url}: {type(exc).__name__}")
        return []

    entries = info.get("entries")
    if entries is None:
        return [{
            "id": f"{info.get('extractor_key', 'src').lower()}-{info['id']}",
            "url": info.get("webpage_url") or url,
            "title": info.get("title") or "",
            "duration": info.get("duration") or 0,
        }]

    out = []
    for e in entries:
        # Tab entries come back as playlists with no URL; the caller already expanded
        # the channel into real tabs, so anything still shaped like this is a dead end.
        if not e or not e.get("url"):
            continue
        out.append({
            "id": f"{(e.get('ie_key') or 'src').lower()}-{e['id']}",
            "url": e["url"],
            "title": e.get("title") or "",
            "duration": e.get("duration") or 0,
        })
    return out


def list_videos(source_url: str, limit: int = 30) -> list[dict]:
    """Newest clippable videos behind a channel, playlist, or video URL.

    Ordered newest first, deduplicated across tabs, and filtered down to sources actually
    long enough to cut a clip out of.
    """
    found: list[dict] = []
    seen: set[str] = set()

    for url in _expand_channel(source_url):
        for video in _list_one(url, limit):
            if video["id"] in seen:
                continue
            duration = video.get("duration") or 0
            # A zero duration means the flat listing did not report one; keep it rather
            # than discard a source on missing metadata.
            if duration and duration < MIN_SOURCE_SECONDS:
                continue
            seen.add(video["id"])
            found.append(video)

    return found[:limit]
