from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Opus 5 picks the clips. This is the one judgement call in the pipeline that decides
# whether a clip pops or flops, and it costs ~$0.15 per hour of source video — the
# transcription below it costs more than the model does.
CLIP_MODEL = os.environ.get("CLIP_MODEL", "claude-opus-5")

# whisper-1 is the only OpenAI transcription model that returns word-level timestamps,
# which the karaoke captions need. $0.006/min.
WHISPER_MODEL = "whisper-1"

# This account is separate from the motivation-shorts channel, so it gets its own
# client secret and its own token file. Never point these at ~/motivation-shorts.
YOUTUBE_CLIENT_SECRET_PATH = ROOT / os.environ.get(
    "YOUTUBE_CLIENT_SECRET_PATH", "youtube_client_secret.json"
)
YOUTUBE_TOKEN_PATH = ROOT / os.environ.get("YOUTUBE_TOKEN_PATH", "youtube_token.json")
YOUTUBE_PRIVACY_STATUS = os.environ.get("YOUTUBE_PRIVACY_STATUS", "private")

# --- Clip shape -------------------------------------------------------------------

TARGET_W, TARGET_H = 1080, 1920
FPS = 30

MIN_CLIP_SECONDS = float(os.environ.get("MIN_CLIP_SECONDS", "20"))
MAX_CLIP_SECONDS = float(os.environ.get("MAX_CLIP_SECONDS", "75"))

# "blur"  -> 16:9 video centred over a blurred fill. Never crops a face out of frame;
#            the safe default for multi-person podcasts and screen-share footage.
# "crop"  -> full-bleed centre crop to 9:16. Higher impact for a single talking head,
#            but cuts off anyone sitting off-centre.
DEFAULT_LAYOUT = os.environ.get("DEFAULT_LAYOUT", "blur")

# Set to "chrome" / "safari" / "firefox" only if YouTube starts refusing downloads.
# It makes yt-dlp reuse your logged-in session, which ties the downloads to your
# account, so leave it empty unless you actually need it.
COOKIES_FROM_BROWSER = os.environ.get("COOKIES_FROM_BROWSER", "").strip() or None

# A source video runs 100-500 MB and is dead weight once its clips are rendered — the
# transcript beside it is what future re-cuts actually need, and that is kilobytes.
# Set KEEP_SOURCES=1 to keep them if you want to re-cut without re-downloading.
KEEP_SOURCES = os.environ.get("KEEP_SOURCES", "").strip() not in ("", "0", "false")

# --- Paths ------------------------------------------------------------------------

DATA = ROOT / "data"
SOURCES_DIR = DATA / "sources"        # downloaded long-form video + cached transcripts
WORK_DIR = DATA / "work"              # per-clip scratch (deleted after a clean render)
PENDING_DIR = DATA / "pending"        # rendered, awaiting your Telegram tap
POSTED_DIR = DATA / "posted"
REJECTED_DIR = DATA / "rejected"
STATE_PATH = DATA / "clips.json"
WATCHLIST_PATH = DATA / "watchlist.json"

for _d in (SOURCES_DIR, WORK_DIR, PENDING_DIR, POSTED_DIR, REJECTED_DIR):
    _d.mkdir(parents=True, exist_ok=True)
