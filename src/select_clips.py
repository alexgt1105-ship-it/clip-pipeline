from __future__ import annotations

import json

import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLIP_MODEL,
    MAX_CLIP_SECONDS,
    MIN_CLIP_SECONDS,
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM = f"""You are a clipper. You find the moments in long-form video that work as \
standalone vertical shorts, and you are judged only on whether a stranger scrolling past \
stops and watches to the end.

What actually earns a clip a slot:
- It opens mid-thought on the most arresting sentence available. No wind-up, no "so anyway", \
no host introducing a topic. The first three seconds carry the whole clip.
- It is self-contained. A viewer with zero context understands it without the surrounding \
conversation.
- It resolves. A story lands its punchline, a claim gets its justification, a question gets \
its answer. A clip that stops mid-argument is a dead clip.
- It has one idea, not three.

Strong candidates: a concrete number or claim that sounds impossible, a contrarian take stated \
plainly, a personal story with a turn in it, a sharp disagreement, a specific piece of advice \
someone could act on today, an admission the speaker seems slightly reluctant to make.

Reject: intros, outros, sponsor reads, housekeeping, logistics, small talk, anything that needs \
on-screen visuals to make sense, anything where the interesting part is only half-said, and \
inside jokes.

Every clip must run between {MIN_CLIP_SECONDS:.0f} and {MAX_CLIP_SECONDS:.0f} seconds. Clips must \
not overlap each other. Score honestly — if a source only holds three good moments, return \
three. Padding the list with filler wastes render time and buries the good clips."""

USER_TEMPLATE = """Source: {title}{by}

Transcript with timestamps:

{transcript}

Find the best standalone vertical clips in this. Return at most {max_clips}, ranked \
strongest first. Timestamps in seconds from the start of the video."""

TOOL = {
    "name": "submit_clips",
    "description": "Submit the chosen clips, strongest first.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {
                            "type": "number",
                            "description": "Clip start in seconds from the video start. Land "
                            "this on the first word of the hook sentence, not before it.",
                        },
                        "end": {
                            "type": "number",
                            "description": "Clip end in seconds. Land this just after the "
                            "last word of the payoff.",
                        },
                        "title": {
                            "type": "string",
                            "description": "YouTube Shorts title, under 90 characters. Written "
                            "as the reason to watch, not a summary. No clickbait punctuation "
                            "and no emoji.",
                        },
                        "hook": {
                            "type": "string",
                            "description": "The exact opening words of the clip, quoted from "
                            "the transcript, so the cut can be verified against the audio.",
                        },
                        "hook_score": {
                            "type": "integer",
                            "description": "1-10. How likely a scroller stops on the first "
                            "three seconds. Be honest; 7+ means you would post it yourself.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One sentence on why this works as a standalone clip.",
                        },
                        "hashtags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "3-6 lowercase hashtags without the # sign.",
                        },
                    },
                    "required": [
                        "start", "end", "title", "hook", "hook_score", "reason", "hashtags",
                    ],
                },
            }
        },
        "required": ["clips"],
    },
}


def _snap_to_words(clip: dict, words: list[dict]) -> dict | None:
    """Move a clip's boundaries onto real word boundaries and pad them slightly.

    The model reads second-resolution timestamps, so its start can land mid-syllable. Cutting
    on the nearest word edge is what keeps a clip from opening on half a word.
    """
    if not words:
        return None

    inside = [w for w in words if w["end"] > clip["start"] and w["start"] < clip["end"]]
    if not inside:
        return None

    start = max(0.0, inside[0]["start"] - 0.15)
    end = inside[-1]["end"] + 0.35

    # Trim from the end rather than the start when over-long: the hook is at the front and
    # is the one part that must survive.
    if end - start > MAX_CLIP_SECONDS:
        end = start + MAX_CLIP_SECONDS
    if end - start < MIN_CLIP_SECONDS:
        return None

    clip = dict(clip)
    clip["start"], clip["end"] = round(start, 3), round(end, 3)
    return clip


def _drop_overlaps(clips: list[dict]) -> list[dict]:
    """Keep the higher-scoring clip when two overlap — two cuts of the same moment compete
    with each other on the same feed."""
    kept: list[dict] = []
    for clip in sorted(clips, key=lambda c: (-c["hook_score"], c["start"])):
        if any(clip["start"] < k["end"] and k["start"] < clip["end"] for k in kept):
            continue
        kept.append(clip)
    return sorted(kept, key=lambda c: c["start"])


def choose(
    transcript: dict,
    meta: dict,
    max_clips: int = 10,
    min_score: int = 6,
) -> list[dict]:
    """Ask the model which moments to cut, then validate its answer against the real words."""
    from transcribe import as_timestamped_text

    text = as_timestamped_text(transcript["segments"])
    by = f"\nChannel: {meta['uploader']}" if meta.get("uploader") else ""
    prompt = USER_TEMPLATE.format(
        title=meta.get("title", "untitled"),
        by=by,
        transcript=text,
        max_clips=max_clips,
    )

    response = client.messages.create(
        model=CLIP_MODEL,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": [{
                "type": "text",
                "text": prompt,
                # Re-running selection on the same source (different max_clips, a retry
                # after a bad batch) then reads the transcript from cache at a tenth of
                # the price.
                "cache_control": {"type": "ephemeral"},
            }],
        }],
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "submit_clips"},
    )

    u = response.usage
    written = getattr(u, "cache_creation_input_tokens", 0) or 0
    read = getattr(u, "cache_read_input_tokens", 0) or 0
    print(
        f"[select] {CLIP_MODEL}: {u.input_tokens + written + read} in "
        f"({read} from cache) / {u.output_tokens} out"
    )

    raw: list[dict] = []
    for block in response.content:
        if block.type == "tool_use":
            # Tool input arrives as parsed JSON, but escaping varies by model — never
            # string-match it.
            raw = json.loads(json.dumps(block.input)).get("clips", [])

    snapped = []
    dropped: list[str] = []
    for clip in raw:
        label = f"{clip.get('title', '?')[:45]!r}"
        if clip.get("hook_score", 0) < min_score:
            dropped.append(f"{label}: scored {clip.get('hook_score')} < {min_score}")
            continue
        fixed = _snap_to_words(clip, transcript["words"])
        if not fixed:
            length = clip["end"] - clip["start"]
            dropped.append(
                f"{label}: {length:.0f}s of speech, under the {MIN_CLIP_SECONDS:.0f}s floor"
            )
            continue
        snapped.append(fixed)

    final = _drop_overlaps(snapped)
    for clip in snapped:
        if clip not in final:
            dropped.append(f"{clip['title'][:45]!r}: overlaps a higher-scoring clip")

    print(f"[select] {len(raw)} proposed -> {len(final)} usable")
    for reason in dropped:
        print(f"[select]   dropped {reason}")
    return final
