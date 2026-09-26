from __future__ import annotations

import json
from pathlib import Path

import requests

import ffmpeg_util
import subs
from config import OPENAI_API_KEY, SOURCES_DIR, WHISPER_MODEL

BASE_URL = "https://api.openai.com/v1"
AUTH_HEADERS = {"Authorization": f"Bearer {OPENAI_API_KEY}"}

# The transcriptions endpoint rejects uploads over 25 MB. At the 32 kbps mono mp3 that
# extract_audio() produces that ceiling is around 100 minutes, so 30-minute chunks leave
# plenty of headroom and keep any single request well inside the timeout.
CHUNK_SECONDS = 30 * 60


def _transcribe_file(audio_path: Path) -> dict:
    with open(audio_path, "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/audio/transcriptions",
            headers=AUTH_HEADERS,
            files={"file": (audio_path.name, f, "audio/mpeg")},
            data={
                "model": WHISPER_MODEL,
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["word", "segment"],
            },
            timeout=900,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Whisper {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def _chunk_audio(audio_path: Path, work_dir: Path) -> list[tuple[Path, float]]:
    """Split audio into <=CHUNK_SECONDS pieces, returning (path, start_offset) pairs.

    Offsets accumulate each chunk's *measured* duration rather than the nominal segment
    length, so word timings stay aligned to the source even when ffmpeg lands a boundary
    slightly early or late.
    """
    total = ffmpeg_util.duration(audio_path)
    if total <= CHUNK_SECONDS:
        return [(audio_path, 0.0)]

    pattern = work_dir / f"{audio_path.stem}-chunk%03d.mp3"
    ffmpeg_util.run([
        "-i", str(audio_path),
        "-f", "segment",
        "-segment_time", str(CHUNK_SECONDS),
        "-c", "copy",
        str(pattern),
    ])

    chunks = sorted(work_dir.glob(f"{audio_path.stem}-chunk*.mp3"))
    out: list[tuple[Path, float]] = []
    offset = 0.0
    for chunk in chunks:
        out.append((chunk, offset))
        offset += ffmpeg_util.duration(chunk)
    return out


def transcript_path(video_path: Path) -> Path:
    return SOURCES_DIR / f"{video_path.stem}.transcript.json"


def transcribe(video_path: Path, force: bool = False) -> dict:
    """Word- and segment-level transcript of `video_path`, cached beside the source.

    Costs $0.006 per minute of audio — about 36 cents for an hour-long podcast, charged
    once per source no matter how many clips come out of it.
    """
    cache = transcript_path(video_path)
    if cache.exists() and not force:
        print(f"[transcribe] reusing {cache.name}")
        return json.loads(cache.read_text())

    work = SOURCES_DIR / f"{video_path.stem}-audio"
    work.mkdir(exist_ok=True)
    audio = ffmpeg_util.extract_audio(video_path, work / "audio.mp3")

    words: list[dict] = []
    segments: list[dict] = []
    chunks = _chunk_audio(audio, work)
    for i, (chunk, offset) in enumerate(chunks, 1):
        size_mb = chunk.stat().st_size / 1e6
        print(f"[transcribe] chunk {i}/{len(chunks)} ({size_mb:.1f} MB, +{offset / 60:.0f}m)")
        payload = _transcribe_file(chunk)
        for w in payload.get("words") or []:
            words.append({
                "word": w["word"],
                "start": w["start"] + offset,
                "end": w["end"] + offset,
            })
        for s in payload.get("segments") or []:
            segments.append({
                "text": s["text"].strip(),
                "start": s["start"] + offset,
                "end": s["end"] + offset,
            })

    result = {"words": words, "segments": segments}
    cache.write_text(json.dumps(result))

    for leftover in work.glob("*"):
        leftover.unlink()
    work.rmdir()

    print(f"[transcribe] {len(words)} words, {len(segments)} segments")
    return result


def get_transcript(
    video_path: Path,
    meta: dict,
    force: bool = False,
    allow_paid: bool = True,
) -> dict:
    """Word-timed transcript for a source, cheapest route first.

    Tries the source's own published caption track (free, and already word-timed) before
    falling back to Whisper at $0.006/min. For an hour-long podcast that is the difference
    between 0 and 36 cents, charged once per source however many clips come out of it.
    """
    cache = transcript_path(video_path)
    if cache.exists() and not force:
        print(f"[transcribe] reusing {cache.name}")
        return json.loads(cache.read_text())

    url = meta.get("webpage_url")
    if url:
        free = subs.fetch(url, video_path.stem)
        if free:
            cache.write_text(json.dumps(free))
            return free

    if not allow_paid:
        raise RuntimeError(
            "No free caption track for this source and paid transcription is disabled "
            "(--no-paid-transcribe). Drop the flag to spend $0.006/min on Whisper."
        )
    return transcribe(video_path, force=force)


def words_in_range(words: list[dict], start: float, end: float) -> list[dict]:
    """Words falling inside [start, end], re-based so the clip's own t=0 is `start`."""
    out = []
    for w in words:
        if w["end"] <= start or w["start"] >= end:
            continue
        out.append({
            "word": w["word"],
            "start": max(0.0, w["start"] - start),
            "end": min(end - start, w["end"] - start),
        })
    return out


def as_timestamped_text(segments: list[dict]) -> str:
    """Transcript rendered as `[mm:ss] text` lines — the form the clip picker reads."""
    lines = []
    for s in segments:
        m, sec = divmod(int(s["start"]), 60)
        lines.append(f"[{m:02d}:{sec:02d}] {s['text']}")
    return "\n".join(lines)
