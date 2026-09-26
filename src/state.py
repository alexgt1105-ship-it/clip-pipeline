from __future__ import annotations

import json
import time
from pathlib import Path

from config import STATE_PATH

_EMPTY = {"sources": {}, "clips": {}}


def load() -> dict:
    if not STATE_PATH.exists():
        return json.loads(json.dumps(_EMPTY))
    data = json.loads(STATE_PATH.read_text())
    for key, default in _EMPTY.items():
        data.setdefault(key, json.loads(json.dumps(default)))
    return data


def save(data: dict) -> None:
    # Write-then-rename: the Telegram listener and the scheduled runner both touch this
    # file, and a half-written clips.json would strand every pending clip.
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STATE_PATH)


def source_done(data: dict, source_id: str) -> bool:
    return source_id in data["sources"]


def mark_source(data: dict, source_id: str, url: str, campaign: str, clip_count: int) -> None:
    data["sources"][source_id] = {
        "url": url,
        "campaign": campaign,
        "clips": clip_count,
        "at": time.time(),
    }


def add_clip(data: dict, clip_id: str, record: dict) -> None:
    record = dict(record)
    record.setdefault("status", "pending")
    record.setdefault("created_at", time.time())
    data["clips"][clip_id] = record


def set_status(data: dict, clip_id: str, status: str, **fields) -> None:
    clip = data["clips"].get(clip_id)
    if not clip:
        return
    clip["status"] = status
    clip.update(fields)


def counts(data: dict) -> dict:
    out: dict = {}
    for clip in data["clips"].values():
        out[clip["status"]] = out.get(clip["status"], 0) + 1
    return out


def posted_for_campaign(data: dict, campaign_id: str) -> int:
    return sum(
        1 for c in data["clips"].values()
        if c.get("campaign") == campaign_id and c["status"] == "posted"
    )
