from __future__ import annotations

import re
import subprocess
from pathlib import Path

import imageio_ffmpeg

# imageio-ffmpeg ships a static ffmpeg binary, so the pipeline runs without Homebrew.
# It does NOT ship ffprobe, which is why duration and resolution are read back out of
# ffmpeg's own stderr below rather than queried properly.
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
_DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+\.\d+)")
_VIDEO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Video:.*?, (\d{2,5})x(\d{2,5})")


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run ffmpeg, raising with its stderr attached — ffmpeg reports the actual reason for
    a failure there, and a bare CalledProcessError hides it."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-y", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}):\n{tail}")
    return proc


def _probe(media_path: Path) -> str:
    # `-i` with no output makes ffmpeg print the stream header and exit non-zero; that's
    # expected, so this deliberately does not go through run().
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(media_path)],
        capture_output=True,
        text=True,
    )
    return proc.stderr


def duration(media_path: Path) -> float:
    """Container-reported duration. Cheap (header read only)."""
    m = _DURATION_RE.search(_probe(media_path))
    if not m:
        return true_duration(media_path)
    h, mm, s = m.groups()
    return int(h) * 3600 + int(mm) * 60 + float(s)


def true_duration(media_path: Path) -> float:
    """Duration by fully decoding. Slow, but correct for files with a missing or lying
    header (concatenated segments, some mp3s)."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(media_path), "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    matches = _TIME_RE.findall(proc.stderr)
    if not matches:
        raise RuntimeError(f"Could not determine duration of {media_path}")
    h, m, s = matches[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def resolution(media_path: Path) -> tuple[int, int]:
    m = _VIDEO_STREAM_RE.search(_probe(media_path))
    if not m:
        raise RuntimeError(f"Could not determine resolution of {media_path}")
    return int(m.group(1)), int(m.group(2))


def extract_audio(video_path: Path, out_path: Path) -> Path:
    """Mono 16 kHz 32 kbps mp3 — the smallest form Whisper still transcribes accurately.
    Keeps roughly 2 hours of speech under the API's 25 MB upload limit.
    """
    run([
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "32k",
        str(out_path),
    ])
    return out_path


def slice_audio(audio_path: Path, out_path: Path, start: float, length: float) -> Path:
    """Copy-cut a span of an audio file (no re-encode)."""
    run([
        "-ss", f"{start:.3f}",
        "-t", f"{length:.3f}",
        "-i", str(audio_path),
        "-c", "copy",
        str(out_path),
    ])
    return out_path


def ffmpeg_bin_dir() -> Path:
    """Directory holding an `ffmpeg`-named symlink to the bundled binary.

    yt-dlp locates ffmpeg by *filename*, and imageio-ffmpeg's binary is called
    `ffmpeg-macos-aarch64-v7.1`, so handing yt-dlp that path directly makes it report
    ffmpeg as missing and refuse to merge separate video/audio streams.
    """
    bin_dir = Path(__file__).resolve().parent.parent / "vendor" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "ffmpeg"
    if not link.exists() or (link.is_symlink() and link.resolve() != Path(FFMPEG)):
        link.unlink(missing_ok=True)
        link.symlink_to(FFMPEG)
    return bin_dir
