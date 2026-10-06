from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import campaigns
import state
import telegram
import upload
from config import TELEGRAM_CHAT_ID, DATA, PENDING_DIR, POSTED_DIR, REJECTED_DIR, ROOT

OFFSET_PATH = DATA / "telegram_offset.txt"
PYTHON = ROOT / "venv" / "bin" / "python"
PIPELINE = ROOT / "src" / "pipeline.py"

HELP = """Commands:
/add <name> <url...>   start a new contract (channel, playlist, or video URLs)
/done <name>           close a contract; the next queued one starts on its own
/campaigns             list contracts
/status                clip counts
/run                   process one more source from the active contract now

Or just send a video/channel URL and it gets clipped."""


def _offset() -> int | None:
    if OFFSET_PATH.exists():
        return int(OFFSET_PATH.read_text().strip())
    return None


def _save_offset(value: int) -> None:
    OFFSET_PATH.write_text(str(value))


def _spawn(args: list[str]) -> None:
    """Run a pipeline command in its own process.

    A source takes minutes to download, transcribe and render. Doing that inline would
    stop the listener answering taps for the whole run, so it is handed off and the
    listener goes straight back to polling.
    """
    subprocess.Popen(
        [str(PYTHON), str(PIPELINE), *args],
        cwd=str(ROOT),
        stdout=open(DATA / "auto.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _handle_callback(update: dict) -> None:
    query = update["callback_query"]
    action, _, clip_id = query["data"].partition(":")
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]

    data = state.load()
    clip = data["clips"].get(clip_id)
    if not clip:
        telegram.answer_callback(query["id"], "Unknown clip")
        return

    if clip["status"] != "pending":
        telegram.answer_callback(query["id"], f"Already {clip['status']}")
        return

    # Look the file up by name: clips.json travels between machines (the Mac, then
    # GitHub runners), so the absolute path it was written with may not exist here.
    path = PENDING_DIR / Path(clip["path"]).name

    if action == "reject":
        if path.exists():
            shutil.move(str(path), str(REJECTED_DIR / path.name))
        state.set_status(data, clip_id, "rejected")
        state.save(data)
        telegram.answer_callback(query["id"], "Skipped")
        telegram.edit_caption(chat_id, message_id, f"Skipped: {clip['title']}")
        return

    telegram.answer_callback(query["id"], "Uploading...")
    try:
        video_id = upload.upload_short(
            path, clip["title"], clip["description"], clip.get("hashtags", [])
        )
    except Exception:
        print(f"[listener] upload failed for {clip_id}:\n{traceback.format_exc()}")
        state.set_status(data, clip_id, "failed")
        state.save(data)
        telegram.edit_caption(
            chat_id, message_id,
            f"Upload FAILED: {clip['title']}\nThe file is still in data/pending.",
        )
        return

    if path.exists():
        shutil.move(str(path), str(POSTED_DIR / path.name))
    state.set_status(data, clip_id, "posted", video_id=video_id)
    state.save(data)
    telegram.edit_caption(
        chat_id, message_id,
        f"Posted: {clip['title']}\nhttps://youtube.com/shorts/{video_id}",
    )


def _handle_message(update: dict, run=None) -> None:
    """`run` takes pipeline args (["auto"], ["clip", url]). The always-on listener spawns
    them as a child process; batch.py queues them and works through them itself."""
    run = run or _spawn
    text = (update["message"].get("text") or "").strip()
    if not text:
        return

    if text.startswith("/add"):
        parts = text.split()
        if len(parts) < 3:
            telegram.send_message("Usage: /add <name> <url> [more urls]")
            return
        try:
            c = campaigns.add(parts[1], parts[2:])
        except ValueError as exc:
            telegram.send_message(str(exc))
            return
        telegram.send_message(
            f"Contract '{c['id']}' added ({c['status']}) over {len(c['sources'])} source(s)."
            + (" It is live now." if c["status"] == "active" else " It starts when the current one finishes.")
        )

    elif text.startswith("/done"):
        parts = text.split()
        if len(parts) < 2:
            telegram.send_message("Usage: /done <name>")
            return
        promoted = campaigns.finish(parts[1], "closed from Telegram")
        telegram.send_message(
            f"Closed '{parts[1]}'."
            + (f" Now running '{promoted['id']}'." if promoted else " Nothing queued behind it.")
        )

    elif text.startswith("/campaigns"):
        data = campaigns.load()
        sdata = state.load()
        if not data["campaigns"]:
            telegram.send_message("No contracts yet. /add <name> <url>")
            return
        lines = []
        for c in data["campaigns"]:
            posted = state.posted_for_campaign(sdata, c["id"])
            cap = f"/{c['clips_target']}" if c.get("clips_target") else ""
            lines.append(f"{c['status']}: {c['id']} - {posted}{cap} posted")
        telegram.send_message("\n".join(lines))

    elif text.startswith("/status"):
        data = state.load()
        counts = state.counts(data)
        telegram.send_message(
            f"sources: {len(data['sources'])}\n"
            + "\n".join(f"{k}: {counts.get(k, 0)}"
                        for k in ("pending", "posted", "rejected", "failed"))
        )

    elif text.startswith("/run"):
        run(["auto"])
        telegram.send_message("Running the next source now. Clips arrive here when ready.")

    elif text.startswith("/help") or text.startswith("/start"):
        telegram.send_message(HELP)

    elif re.match(r"^https?://\S+$", text):
        run(["clip", text])
        telegram.send_message("Clipping that now. Results land here shortly.")


def _chat_id(update: dict) -> str:
    if "callback_query" in update:
        return str(update["callback_query"]["message"]["chat"]["id"])
    return str((update.get("message") or {}).get("chat", {}).get("id", ""))


def dispatch(update: dict, run=None) -> None:
    # The bot's username is findable by anyone, and every command here spends money or
    # posts to the channel, so only the owner's chat is listened to.
    if _chat_id(update) != str(TELEGRAM_CHAT_ID):
        return
    try:
        if "callback_query" in update:
            _handle_callback(update)
        elif "message" in update:
            _handle_message(update, run)
    except Exception:
        print(f"[listener] update failed:\n{traceback.format_exc()}")


def main() -> None:
    print("[listener] polling. Ctrl-C to stop.")
    offset = _offset()
    while True:
        try:
            updates = telegram.get_updates(offset=offset, timeout=50)
        except Exception as exc:  # noqa: BLE001 - a dropped connection must not end the daemon
            print(f"[listener] poll error: {type(exc).__name__}: {exc}")
            time.sleep(10)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            _save_offset(offset)
            dispatch(update)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[listener] stopped")
