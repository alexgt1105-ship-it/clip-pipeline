"""One self-contained run, for machines that are not on all day (GitHub Actions).

listener.py assumes something is always polling Telegram. A scheduled runner is not, so
each run does the whole cycle and exits:

  1. handle every tap and command queued in Telegram since the last run,
  2. advance the active contract by one source (plus any /run or URL just received),
  3. stay on Telegram for LISTEN_SECONDS so the new clips can be approved straight away,
  4. exit. Taps that arrive later wait in Telegram (it holds them 24h) for the next run.
"""
from __future__ import annotations

import os
import sys
import time
import traceback

import listener
import pipeline
import telegram

LISTEN_SECONDS = int(os.environ.get("LISTEN_SECONDS", "600"))

# A /run or URL received while listening is clipped in the same run, and buys the
# listening window again so its clips can be tapped. Capped so a chatty evening cannot
# hold a runner for hours.
MAX_JOBS_PER_RUN = 4


def _poll(timeout: int, queue: list[list[str]]) -> None:
    offset = listener._offset()
    updates = telegram.get_updates(offset=offset, timeout=timeout)
    for update in updates:
        listener._save_offset(update["update_id"] + 1)
        listener.dispatch(update, run=queue.append)


def _work(job: list[str]) -> None:
    try:
        if job[0] == "auto":
            pipeline.run_auto()
        elif job[0] == "clip":
            pipeline.process_source(job[1])
    except Exception:
        print(f"[batch] {job[0]} failed:\n{traceback.format_exc()}")
        telegram.send_message(f"Clip run hit an error on '{job[0]}'. Details are in the run log.")


def main() -> int:
    queue: list[list[str]] = []

    # 1. Whatever piled up since the last run. timeout=0 returns immediately.
    _poll(0, queue)

    # 2. The scheduled source, plus anything just asked for. /run duplicates the
    #    scheduled auto, so it only counts once.
    if os.environ.get("SKIP_AUTO") != "1" and ["auto"] not in queue:
        queue.insert(0, ["auto"])
    if os.environ.get("CLIP_URL"):
        queue.append(["clip", os.environ["CLIP_URL"]])

    jobs = 0
    deadline = time.time()
    while True:
        while queue and jobs < MAX_JOBS_PER_RUN:
            _work(queue.pop(0))
            jobs += 1
            deadline = time.time() + LISTEN_SECONDS
        if queue:
            telegram.send_message(
                f"This run already clipped {jobs} sources, so {len(queue)} request(s) were "
                "dropped. Send them again after the next scheduled run."
            )
            queue.clear()

        remaining = int(deadline - time.time())
        if remaining <= 0:
            break
        try:
            _poll(min(50, remaining), queue)
        except Exception as exc:  # noqa: BLE001 - a dropped poll must not end the run
            print(f"[batch] poll error: {type(exc).__name__}: {exc}")
            time.sleep(5)

    print(f"[batch] done after {jobs} job(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
