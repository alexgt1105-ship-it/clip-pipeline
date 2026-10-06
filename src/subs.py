from __future__ import annotations

import json
from pathlib import Path

import yt_dlp

from config import SOURCES_DIR, YTDLP_AUTH

LANGS = ["en", "en-orig", "en-US", "en-GB"]

# Below this many segments-per-caption-event, a track is carrying whole phrases rather
# than individual words and its timings have to be interpolated.
WORD_LEVEL_RATIO = 1.5


def _expand(text: str, start: float, end: float) -> list[dict]:
    """Split a caption phrase into words, sharing its span out by word length.

    Uploader-written caption tracks time whole lines, not words. Spreading the line's
    duration across its words in proportion to their length puts each highlight within
    about a tenth of a second of the real thing — close enough to read as in sync, and
    the only alternative for these tracks is paying to re-transcribe audio that already
    has a perfectly good transcript.
    """
    tokens = text.split()
    if not tokens:
        return []

    span = max(end - start, 0.08 * len(tokens))
    total = sum(len(t) for t in tokens) or len(tokens)

    out = []
    cursor = start
    for token in tokens:
        share = span * (len(token) / total)
        out.append({
            "word": token,
            "start": round(cursor, 3),
            "end": round(cursor + max(share, 0.08), 3),
        })
        cursor += share
    return out


def _parse_json3(payload: dict) -> dict:
    """Convert a YouTube json3 caption track to the same {words, segments} shape Whisper
    produces, whether it is word-timed (auto-generated) or phrase-timed (uploader)."""
    events = []
    for event in payload.get("events") or []:
        # Rolling auto-caption tracks emit "aAppend" events repeating text the previous
        # event already covered; counting them duplicates every word.
        if event.get("aAppend"):
            continue
        segs = [s for s in (event.get("segs") or []) if (s.get("utf8") or "").strip()]
        if not segs:
            continue
        events.append((event.get("tStartMs", 0) / 1000.0,
                       event.get("dDurationMs", 0) / 1000.0,
                       segs))

    if not events:
        return {"words": [], "segments": [], "word_level": False}

    seg_count = sum(len(s) for _, _, s in events)
    word_level = (seg_count / len(events)) >= WORD_LEVEL_RATIO

    words: list[dict] = []
    segments: list[dict] = []

    for i, (ev_start, ev_dur, segs) in enumerate(events):
        ev_end = ev_start + ev_dur if ev_dur else (
            events[i + 1][0] if i + 1 < len(events) else ev_start + 3.0
        )
        # A missing or overlong duration would stretch one caption over the next; clamp
        # every event to the start of the one after it.
        if i + 1 < len(events):
            ev_end = min(ev_end, events[i + 1][0])
        ev_end = max(ev_end, ev_start + 0.2)

        pieces = []
        for j, seg in enumerate(segs):
            p_start = ev_start + seg.get("tOffsetMs", 0) / 1000.0
            p_end = (
                ev_start + segs[j + 1].get("tOffsetMs", 0) / 1000.0
                if j + 1 < len(segs) else ev_end
            )
            pieces.append((seg["utf8"], p_start, max(p_end, p_start + 0.08)))

        event_words = []
        for text, p_start, p_end in pieces:
            event_words.extend(_expand(text, p_start, p_end))

        if not event_words:
            continue

        words.extend(event_words)
        segments.append({
            "text": " ".join(w["word"] for w in event_words),
            "start": round(ev_start, 3),
            "end": round(ev_end, 3),
        })

    words.sort(key=lambda w: w["start"])
    return {"words": words, "segments": segments, "word_level": word_level}


def _download(url: str, work: Path, auto_only: bool) -> dict | None:
    for f in work.glob("*.json3"):
        f.unlink()

    opts = {
        "skip_download": True,
        "writesubtitles": not auto_only,
        "writeautomaticsub": True,
        "subtitleslangs": LANGS,
        "subtitlesformat": "json3",
        "outtmpl": str(work / "track.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        **YTDLP_AUTH,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:  # noqa: BLE001 - any extractor failure just means "pay instead"
        print(f"[subs] caption fetch failed ({type(exc).__name__})")
        return None

    tracks = sorted(work.glob("*.json3"))
    if not tracks:
        return None

    def rank(p: Path) -> int:
        lang = p.name.split(".")[-2]
        return LANGS.index(lang) if lang in LANGS else len(LANGS)

    chosen = sorted(tracks, key=rank)[0]
    result = _parse_json3(json.loads(chosen.read_text()))
    result["lang"] = chosen.name.split(".")[-2]
    return result


def fetch(url: str, stem: str) -> dict | None:
    """Pull a word-timed transcript from the source's own caption track, free.

    Prefers whichever available track is genuinely word-timed: YouTube's auto-generated
    track carries per-word offsets, while an uploader's track is timed per line and has
    to be interpolated. Returns None when the source publishes no usable captions, in
    which case the caller falls back to paid transcription.
    """
    work = SOURCES_DIR / f"{stem}-subs"
    work.mkdir(parents=True, exist_ok=True)

    try:
        best = _download(url, work, auto_only=False)
        if best and not best["word_level"]:
            auto = _download(url, work, auto_only=True)
            if auto and auto["word_level"] and len(auto["words"]) >= 50:
                print("[subs] uploader track is line-timed; using the word-timed auto track")
                best = auto
    finally:
        for f in work.glob("*"):
            f.unlink()
        work.rmdir()

    if not best or len(best["words"]) < 50:
        print("[subs] no usable caption track; will transcribe instead")
        return None

    timing = "word-timed" if best["word_level"] else "line-timed (interpolated)"
    print(f"[subs] free captions: {len(best['words'])} words, {best['lang']}, {timing}")
    return {"words": best["words"], "segments": best["segments"]}
