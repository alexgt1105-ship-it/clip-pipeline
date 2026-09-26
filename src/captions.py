from __future__ import annotations

from pathlib import Path

from config import TARGET_H, TARGET_W

CHUNK_SIZE = 4
GAP_BREAK_SECONDS = 0.6

HIGHLIGHT_COLOR = "&H0000E5FF&"  # ASS is &HAABBGGRR& -> warm amber
BASE_COLOR = "&H00FFFFFF&"

# Where the caption block sits, as a margin up from the bottom edge. In the blurred-fill
# layout the 16:9 video is centred and leaves dead space below it, so captions drop into
# that space instead of covering the speaker's face.
MARGIN_V = {"blur": 470, "crop": 300}


def _header(margin_v: int) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_W}
PlayResY: {TARGET_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,74,{BASE_COLOR},&H000000FF&,&H00000000&,&H64000000&,1,0,0,0,100,100,0,0,1,7,3,2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def _chunk_words(words: list[dict]) -> list[list[dict]]:
    """Group words into short on-screen phrases, breaking on a pause even mid-phrase — a
    caption that stays up through a silence reads as lag."""
    chunks: list[list[dict]] = []
    current: list[dict] = []

    for word in words:
        if current and (
            len(current) >= CHUNK_SIZE
            or word["start"] - current[-1]["end"] > GAP_BREAK_SECONDS
        ):
            chunks.append(current)
            current = []
        current.append(word)

    if current:
        chunks.append(current)
    return chunks


def build_ass(words: list[dict], out_path: Path, layout: str = "blur") -> Path:
    """Karaoke captions: the whole phrase is on screen, the word being spoken is highlighted.

    Word timings must already be relative to the clip's own start.
    """
    lines = [_header(MARGIN_V.get(layout, MARGIN_V["blur"]))]

    for chunk in _chunk_words(words):
        chunk_end = chunk[-1]["end"]
        for i, word in enumerate(chunk):
            seg_start = word["start"]
            # Hold each word's highlight until the next one starts, so there is never an
            # un-highlighted gap between words.
            seg_end = chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk_end
            if seg_end <= seg_start:
                continue

            rendered = []
            for j, w in enumerate(chunk):
                text = _escape(w["word"].strip())
                if not text:
                    continue
                if j == i:
                    rendered.append(f"{{\\c{HIGHLIGHT_COLOR}}}{text}{{\\c{BASE_COLOR}}}")
                else:
                    rendered.append(text)

            lines.append(
                f"Dialogue: 0,{_ass_time(seg_start)},{_ass_time(seg_end)},"
                f"Default,,0,0,0,,{' '.join(rendered)}"
            )

    out_path.write_text("\n".join(lines) + "\n")
    return out_path
