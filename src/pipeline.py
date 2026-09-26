from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import campaigns
import ingest
import render
import select_clips
import state
import telegram
import transcribe
from config import DEFAULT_LAYOUT, KEEP_SOURCES, PENDING_DIR


def _description(clip: dict, meta: dict) -> str:
    lines = [clip["title"], ""]
    if meta.get("webpage_url"):
        # Crediting the source is both the decent thing and, on most paid clipping
        # programs, a condition of getting paid.
        lines.append(f"Full episode: {meta['webpage_url']}")
    if meta.get("uploader"):
        lines.append(f"Credit: {meta['uploader']}")
    lines.append("")
    lines.append(" ".join(f"#{t.lstrip('#')}" for t in clip.get("hashtags", [])))
    return "\n".join(lines)


def process_source(
    target: str,
    campaign_id: str = "manual",
    max_clips: int = 5,
    layout: str | None = None,
    allow_paid: bool = True,
    min_score: int = 6,
    dry_run: bool = False,
) -> int:
    """Run one long-form source end to end. Returns the number of clips sent for review."""
    layout = layout or DEFAULT_LAYOUT

    source, meta = ingest.resolve(target)
    print(f"[pipeline] {meta['title']} ({meta['duration'] / 60:.0f} min)")

    transcript = transcribe.get_transcript(source, meta, allow_paid=allow_paid)
    clips = select_clips.choose(
        transcript, meta, max_clips=max_clips, min_score=min_score
    )
    if not clips:
        print("[pipeline] nothing worth clipping in this source")
        return 0

    if dry_run:
        for i, clip in enumerate(clips, 1):
            mins, secs = divmod(int(clip["start"]), 60)
            print(
                f"  {i}. [{mins:02d}:{secs:02d}] {clip['end'] - clip['start']:.0f}s  "
                f"score {clip['hook_score']}  {clip['title']}"
            )
            print(f"     hook: {clip['hook']}")
            print(f"     why:  {clip['reason']}")
        return 0

    data = state.load()
    sent = 0
    for i, clip in enumerate(clips, 1):
        clip_id = f"{meta['source_id']}-{i:02d}"
        out_path = PENDING_DIR / f"{clip_id}.mp4"
        words = transcribe.words_in_range(transcript["words"], clip["start"], clip["end"])

        try:
            render.render(
                source, out_path, clip["start"], clip["end"], words, layout=layout
            )
        except Exception:
            # One bad cut should not cost the whole source; the rest still go out.
            print(f"[pipeline] render failed for {clip_id}:\n{traceback.format_exc()}")
            continue

        mins, secs = divmod(int(clip["start"]), 60)
        caption = (
            f"{clip['title']}\n\n"
            f"score {clip['hook_score']}/10 - {clip['end'] - clip['start']:.0f}s - "
            f"from {mins:02d}:{secs:02d}\n"
            f"{clip['reason']}\n\n"
            f"source: {meta['title'][:80]}"
        )

        try:
            resp = telegram.send_for_approval(out_path, clip_id, caption)
            message_id = resp["result"]["message_id"]
            chat_id = resp["result"]["chat"]["id"]
        except Exception:
            print(f"[pipeline] telegram send failed for {clip_id}:\n{traceback.format_exc()}")
            message_id = chat_id = None

        state.add_clip(data, clip_id, {
            "campaign": campaign_id,
            "source_id": meta["source_id"],
            "source_title": meta["title"],
            "source_url": meta.get("webpage_url", ""),
            "path": str(out_path),
            "title": clip["title"],
            "description": _description(clip, meta),
            "hashtags": clip.get("hashtags", []),
            "hook_score": clip["hook_score"],
            "start": clip["start"],
            "end": clip["end"],
            "message_id": message_id,
            "chat_id": chat_id,
        })
        sent += 1

    state.mark_source(data, meta["source_id"], meta.get("webpage_url", target), campaign_id, sent)
    state.save(data)

    if not KEEP_SOURCES and source.exists():
        freed = source.stat().st_size / 1e6
        source.unlink()
        print(f"[pipeline] removed the {freed:.0f} MB source (transcript kept)")
    print(f"[pipeline] {sent} clip(s) waiting for your tap in Telegram")
    return sent


def run_auto(limit_sources: int = 1, dry_run: bool = False) -> None:
    """Advance the active contract by one source video.

    This is the scheduled entry point. It picks the contract that is currently active,
    finds its newest video that has not been clipped yet, and runs it. When a contract
    hits its target or runs out of video, it is closed, the next queued contract is
    promoted, and that new contract's first source is clipped in the same run — so a
    contract ending never costs a cycle of idle time.
    """
    processed = 0
    # Bounded so a run of empty or exhausted contracts terminates instead of spinning.
    for _ in range(len(campaigns.load()["campaigns"]) + 1):
        if processed >= limit_sources:
            return

        campaign = campaigns.active()
        if not campaign:
            print("[auto] no active campaign. Add one: campaign add <id> <url>")
            return

        sdata = state.load()
        posted = state.posted_for_campaign(sdata, campaign["id"])
        target = campaign.get("clips_target") or 0
        if target and posted >= target:
            _finish(campaign, f"hit target of {target} posted clips")
            continue

        candidates = []
        for source_url in campaign["sources"]:
            try:
                candidates.extend(campaigns.list_videos(source_url))
            except Exception as exc:  # noqa: BLE001 - one bad source must not stall the rest
                print(f"[auto] could not list {source_url}: {type(exc).__name__}: {exc}")

        fresh = [v for v in candidates if not state.source_done(sdata, v["id"])]
        if not fresh:
            _finish(campaign, "no unclipped source video left")
            continue

        nxt = fresh[0]
        print(f"[auto] campaign {campaign['id']}: next source {nxt['title'][:70]!r}")
        process_source(
            nxt["url"],
            campaign_id=campaign["id"],
            max_clips=campaign.get("max_clips_per_video", 5),
            layout=campaign.get("layout"),
            dry_run=dry_run,
        )
        processed += 1


def _finish(campaign: dict, reason: str) -> None:
    promoted = campaigns.finish(campaign["id"], reason)
    msg = f"Contract '{campaign['id']}' finished: {reason}."
    msg += f" Now running '{promoted['id']}'." if promoted else " No contract queued behind it."
    print(f"[auto] {msg}")
    try:
        telegram.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        print(f"[auto] could not notify: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pipeline", description="Long-form -> vertical clips")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_clip = sub.add_parser("clip", help="clip one URL or local file right now")
    p_clip.add_argument("target")
    p_clip.add_argument("--max-clips", type=int, default=5)
    p_clip.add_argument("--layout", choices=["blur", "crop"], default=None)
    p_clip.add_argument("--min-score", type=int, default=6)
    p_clip.add_argument("--campaign", default="manual")
    p_clip.add_argument("--no-paid-transcribe", action="store_true",
                        help="skip sources with no free caption track instead of paying Whisper")
    p_clip.add_argument("--dry-run", action="store_true",
                        help="print the chosen moments without rendering or sending")

    p_auto = sub.add_parser("auto", help="advance the active contract by one source video")
    p_auto.add_argument("--dry-run", action="store_true")

    p_camp = sub.add_parser("campaign", help="manage contracts")
    csub = p_camp.add_subparsers(dest="camp_cmd", required=True)
    c_add = csub.add_parser("add")
    c_add.add_argument("id")
    c_add.add_argument("sources", nargs="+", help="channel, playlist, or video URLs")
    c_add.add_argument("--target", type=int, default=0, help="stop after N posted clips")
    c_add.add_argument("--per-video", type=int, default=5)
    c_add.add_argument("--layout", choices=["blur", "crop"], default=None)
    c_add.add_argument("--notes", default="")
    csub.add_parser("list")
    c_done = csub.add_parser("done")
    c_done.add_argument("id")

    sub.add_parser("status", help="show clip counts")

    args = parser.parse_args()

    if args.cmd == "clip":
        process_source(
            args.target,
            campaign_id=args.campaign,
            max_clips=args.max_clips,
            layout=args.layout,
            allow_paid=not args.no_paid_transcribe,
            min_score=args.min_score,
            dry_run=args.dry_run,
        )

    elif args.cmd == "auto":
        run_auto(dry_run=args.dry_run)

    elif args.cmd == "campaign":
        if args.camp_cmd == "add":
            c = campaigns.add(
                args.id, args.sources,
                clips_target=args.target,
                max_clips_per_video=args.per_video,
                layout=args.layout,
                notes=args.notes,
            )
            print(f"Added '{c['id']}' ({c['status']}) over {len(c['sources'])} source(s)")
        elif args.camp_cmd == "list":
            data = campaigns.load()
            sdata = state.load()
            if not data["campaigns"]:
                print("No campaigns yet.")
            for c in data["campaigns"]:
                posted = state.posted_for_campaign(sdata, c["id"])
                cap = f"/{c['clips_target']}" if c.get("clips_target") else ""
                print(f"{c['status']:<7} {c['id']:<22} {posted}{cap} posted   {c.get('notes', '')}")
        elif args.camp_cmd == "done":
            _finish({"id": args.id}, "closed by hand")

    elif args.cmd == "status":
        data = state.load()
        counts = state.counts(data)
        print(f"sources processed: {len(data['sources'])}")
        for status in ("pending", "posted", "rejected", "failed"):
            print(f"  {status:<9} {counts.get(status, 0)}")


if __name__ == "__main__":
    sys.exit(main())
