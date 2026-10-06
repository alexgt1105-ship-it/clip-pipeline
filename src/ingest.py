from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yt_dlp

import ffmpeg_util
from config import SOURCES_DIR, YTDLP_AUTH

# YouTube gates some videos behind a player client that the default extraction path
# cannot use. Each fallback trades quality for reach — android in particular often
# only offers 240p — so they are tried strictly in order and only after a failure.
PLAYER_CLIENT_FALLBACKS = [None, "tv_simply", "web_safari", "ios", "android"]


def _slug(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit] or "source"


def _meta_path(video_path: Path) -> Path:
    return video_path.with_suffix(".meta.json")


def _save_meta(video_path: Path, meta: dict) -> dict:
    _meta_path(video_path).write_text(json.dumps(meta, indent=2))
    return meta


def load_meta(video_path: Path) -> dict:
    path = _meta_path(video_path)
    if path.exists():
        return json.loads(path.read_text())
    return {
        "source_id": video_path.stem,
        "title": video_path.stem,
        "uploader": "",
        "webpage_url": "",
        "duration": ffmpeg_util.duration(video_path),
    }


def fetch(url: str, force: bool = False) -> tuple[Path, dict]:
    """Download the long-form source at `url` into data/sources and return (path, metadata).

    Anything yt-dlp supports works here — YouTube videos, Twitch VODs, podcast episodes,
    X posts — so the pipeline is not tied to one channel or one kind of stream.
    Re-running with the same URL reuses the existing download instead of re-fetching.
    """
    probe_opts = {"quiet": True, "no_warnings": True, "skip_download": True, **YTDLP_AUTH}
    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    source_id = f"{info.get('extractor_key', 'src').lower()}-{info['id']}"
    stem = f"{source_id}-{_slug(info.get('title') or '')}"
    existing = sorted(SOURCES_DIR.glob(f"{source_id}-*.mp4"))

    meta = {
        "source_id": source_id,
        "title": info.get("title") or source_id,
        "uploader": info.get("uploader") or info.get("channel") or "",
        "webpage_url": info.get("webpage_url") or url,
        "duration": float(info.get("duration") or 0.0),
    }

    if existing and not force:
        print(f"[ingest] reusing {existing[0].name}")
        return existing[0], _save_meta(existing[0], meta)

    out_path = SOURCES_DIR / f"{stem}.mp4"
    download_opts = {
        # Cap at 1080p: a 9:16 crop out of 1080p already exceeds what Shorts serves, and
        # 4K source would multiply download time and render cost for no visible gain.
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(SOURCES_DIR / f"{stem}.%(ext)s"),
        "ffmpeg_location": str(ffmpeg_util.ffmpeg_bin_dir()),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
    }
    print(f"[ingest] downloading {meta['title']!r} ({meta['duration'] / 60:.0f} min)")
    _download_with_fallback(url, download_opts)

    if not out_path.exists():
        # yt-dlp may land on a different container than requested (e.g. .mkv when no mp4
        # stream pair exists); take whatever it actually produced for this stem.
        produced = [p for p in SOURCES_DIR.glob(f"{stem}.*") if p.suffix != ".json"]
        if not produced:
            raise RuntimeError(f"yt-dlp produced no file for {url}")
        out_path = produced[0]

    meta["duration"] = ffmpeg_util.duration(out_path)
    return out_path, _save_meta(out_path, meta)


def _download_with_fallback(url: str, base_opts: dict) -> None:
    """Download, retrying through alternate player clients before giving up.

    A single video that YouTube serves awkwardly should not stall a whole contract, so
    each client is tried in turn and only an all-failed run raises.
    """
    errors = []
    for client in PLAYER_CLIENT_FALLBACKS:
        opts = dict(base_opts)
        if client:
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        opts.update(YTDLP_AUTH)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if client:
                print(f"[ingest] fell back to player client {client!r}")
            return
        except Exception as exc:  # noqa: BLE001 - try the next client, report all at the end
            errors.append(f"{client or 'default'}: {str(exc)[:120]}")

    raise RuntimeError(
        "every player client failed for this source:\n  " + "\n  ".join(errors)
    )


def adopt_local(path: Path) -> tuple[Path, dict]:
    """Register a video already on disk as a source, copying it into data/sources."""
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    dest = SOURCES_DIR / f"local-{_slug(path.stem)}{path.suffix}"
    if dest.resolve() != path:
        shutil.copy2(path, dest)

    meta = {
        "source_id": dest.stem,
        "title": path.stem,
        "uploader": "",
        "webpage_url": "",
        "duration": ffmpeg_util.duration(dest),
    }
    return dest, _save_meta(dest, meta)


def resolve(target: str, force: bool = False) -> tuple[Path, dict]:
    """Accept either a URL or a local file path."""
    if re.match(r"^https?://", target):
        return fetch(target, force=force)
    return adopt_local(Path(target))
