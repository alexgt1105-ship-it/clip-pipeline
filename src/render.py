from __future__ import annotations

import shutil
from pathlib import Path

import captions
import ffmpeg_util
from config import DEFAULT_LAYOUT, FPS, TARGET_H, TARGET_W, WORK_DIR

# Blurred-fill: the source keeps its own aspect ratio, centred over a blurred, darkened
# copy of itself. Nothing is ever cropped out of frame, which matters for two-person
# podcasts and anything with on-screen text.
_BLUR = (
    "[0:v]split=2[bg][fg];"
    f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
    f"crop={TARGET_W}:{TARGET_H},boxblur=22:2,eq=brightness=-0.18[bgb];"
    f"[fg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fgs];"
    "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[comp]"
)

# Centre crop: full-bleed 9:16, higher impact for a single talking head, but it throws
# away everything outside the middle third of the frame.
_CROP = (
    f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
    f"crop={TARGET_W}:{TARGET_H}[comp]"
)


def _filter_complex(layout: str, ass_name: str | None) -> str:
    base = _CROP if layout == "crop" else _BLUR
    # ass= takes a filename relative to ffmpeg's cwd, so render() runs ffmpeg inside the
    # clip's work directory rather than trying to escape an absolute path into a filter.
    tail = f"ass={ass_name}," if ass_name else ""
    return f"{base};[comp]{tail}fps={FPS},format=yuv420p[v]"


def render(
    source: Path,
    out_path: Path,
    start: float,
    end: float,
    words: list[dict],
    layout: str = DEFAULT_LAYOUT,
    burn_captions: bool = True,
) -> Path:
    """Cut [start, end] out of `source` and render it as a captioned vertical clip."""
    work = WORK_DIR / out_path.stem
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    ass_name = None
    if burn_captions and words:
        ass_name = "captions.ass"
        captions.build_ass(words, work / ass_name, layout=layout)

    tmp_out = work / "out.mp4"
    ffmpeg_util.run(
        [
            # -ss before -i seeks by keyframe but is exact once the stream is re-encoded,
            # and it avoids decoding the hour of video that precedes the clip.
            "-ss", f"{start:.3f}",
            "-t", f"{end - start:.3f}",
            "-i", str(source.resolve()),
            "-filter_complex", _filter_complex(layout, ass_name),
            "-map", "[v]",
            # Trailing "?" makes the audio stream optional — a silent source (some
            # screen recordings, a video-only VOD) should still render, not abort.
            "-map", "0:a:0?",
            # Shorts/TikTok/Reels all normalise toward -14 LUFS; doing it here stops quiet
            # podcast audio from being played back at a whisper.
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-movflags", "+faststart",
            tmp_out.name,
        ],
        cwd=work,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_out), str(out_path))
    shutil.rmtree(work)

    size_mb = out_path.stat().st_size / 1e6
    print(f"[render] {out_path.name} ({end - start:.1f}s, {size_mb:.1f} MB, {layout})")
    return out_path
