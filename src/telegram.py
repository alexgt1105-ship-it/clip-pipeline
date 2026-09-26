from __future__ import annotations

import json
from pathlib import Path

import requests

import ffmpeg_util
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WORK_DIR

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Bots may upload at most 50 MB. Long clips at crf 20 can brush that, so anything close
# gets a smaller preview — the full-quality file stays on disk and is what gets uploaded.
PREVIEW_THRESHOLD_MB = 45


def _preview(video_path: Path) -> Path:
    preview = WORK_DIR / f"{video_path.stem}-preview.mp4"
    ffmpeg_util.run([
        "-i", str(video_path),
        "-vf", "scale=720:1280",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        str(preview),
    ])
    return preview


def send_for_approval(video_path: Path, clip_id: str, caption: str) -> dict:
    keyboard = {
        "inline_keyboard": [[
            {"text": "Post it", "callback_data": f"approve:{clip_id}"},
            {"text": "Skip", "callback_data": f"reject:{clip_id}"},
        ]]
    }

    send_path = video_path
    temp = None
    if video_path.stat().st_size / 1e6 > PREVIEW_THRESHOLD_MB:
        temp = send_path = _preview(video_path)

    try:
        with open(send_path, "rb") as f:
            resp = requests.post(
                f"{API_BASE}/sendVideo",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                    "reply_markup": json.dumps(keyboard),
                },
                files={"video": f},
                timeout=300,
            )
        resp.raise_for_status()
        return resp.json()
    finally:
        if temp and temp.exists():
            temp.unlink()


def send_message(text: str) -> None:
    requests.post(
        f"{API_BASE}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4096]},
        timeout=30,
    ).raise_for_status()


def answer_callback(callback_query_id: str, text: str) -> None:
    # Best-effort: Telegram expires callback IDs about a minute after the tap, so by the
    # time a slow upload finishes this usually 400s. The real confirmation the user sees
    # is edit_caption below, which keys off a message ID and never expires.
    try:
        requests.post(
            f"{API_BASE}/answerCallbackQuery",
            data={"callback_query_id": callback_query_id, "text": text[:200]},
            timeout=30,
        ).raise_for_status()
    except requests.RequestException as exc:
        print(f"[telegram] answer_callback failed (non-fatal): {exc}")


def edit_caption(chat_id: int, message_id: int, caption: str) -> None:
    try:
        requests.post(
            f"{API_BASE}/editMessageCaption",
            data={"chat_id": chat_id, "message_id": message_id, "caption": caption[:1024]},
            timeout=30,
        ).raise_for_status()
    except requests.RequestException as exc:
        print(f"[telegram] edit_caption failed (non-fatal): {exc}")


def get_updates(offset: int | None = None, timeout: int = 30) -> list[dict]:
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=timeout + 15)
    resp.raise_for_status()
    return resp.json()["result"]
