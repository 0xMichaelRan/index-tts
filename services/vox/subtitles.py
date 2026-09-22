"""
ASS subtitle pipeline for Vox video rendering.

Generates word-level karaoke ASS subtitle files from stable-whisper
alignment data and burns them into the video stream.
"""

from __future__ import annotations

import os
import subprocess

from services.common.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Bundled font directory
# ---------------------------------------------------------------------------

# Resolved relative to this file's location so it works regardless of the
# current working directory when the worker is launched.
# services/vox/subtitles.py → two parents up → project root → assets/fonts/
from pathlib import Path

_FONTS_DIR: str = str(Path(__file__).parent.parent.parent / "assets" / "fonts")


# ---------------------------------------------------------------------------
# Subtitle helpers
# ---------------------------------------------------------------------------


def _sec_to_ass_time(sec: float) -> str:
    """Convert seconds to ASS time string: H:MM:SS.CC (centiseconds)."""
    total_cs = round(sec * 100)
    hours = total_cs // 360000
    total_cs %= 360000
    minutes = total_cs // 6000
    total_cs %= 6000
    secs = total_cs // 100
    centis = total_cs % 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _has_cjk_chars(text: str) -> bool:
    """Return True if text contains CJK (Chinese/Japanese/Korean) characters."""
    for c in text:
        cp = ord(c)
        if (
            0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
            or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
            or 0xAC00 <= cp <= 0xD7A3  # Hangul Syllables
            or 0x3040 <= cp <= 0x30FF  # Hiragana / Katakana
        ):
            return True
    return False


def _group_words_by_beats(
    words: list[dict],
    beat_narrations: list[str],
) -> list[list[dict]]:
    """
    Assign alignment words to beats using the same token-count slicing
    as ``ScriptGuidedAligner``.  This guarantees the subtitle text per beat
    matches the word-level timestamps exactly.

    Returns a list of length ``len(beat_narrations)``; each element is the
    sub-list of words belonging to that beat.
    """
    # Import here to avoid a circular import (aligner imports nothing from subtitles)
    from services.vox.aligner import ScriptGuidedAligner

    n = len(beat_narrations)
    if n == 0:
        return []
    token_counts = [ScriptGuidedAligner._token_count(narr) for narr in beat_narrations]
    total_tokens = sum(token_counts)
    available = len(words)

    if total_tokens > available:
        scale = available / total_tokens if total_tokens > 0 else 1.0
        min_count = 1 if available >= n else 0
        token_counts = [max(min_count, round(c * scale)) for c in token_counts]
        idx = n - 1
        while sum(token_counts) > available and idx >= 0:
            if token_counts[idx] > min_count:
                token_counts[idx] -= 1
            else:
                idx -= 1
        idx = n - 1
        while sum(token_counts) > available and idx >= 0:
            if token_counts[idx] > 0:
                token_counts[idx] -= 1
            idx -= 1

    groups: list[list[dict]] = []
    cursor = 0
    for count in token_counts:
        end = min(cursor + count, available)
        groups.append(list(words[cursor:end]))
        cursor = end

    return groups


def _build_karaoke_text(
    words: list[dict],
    win_start: float,
    win_end: float,
) -> str:
    """
    Build an ASS karaoke text string with ``\\k`` timing tags.

    ``{\\kN}text`` means *text* is highlighted for N centiseconds;
    highlights are applied sequentially from the subtitle start time.
    The total centiseconds of all ``\\k`` spans equals
    ``(win_end - win_start) * 100`` so playback never drifts.
    """
    win_start_cs = round(win_start * 100)
    win_end_cs = round(win_end * 100)
    current_cs = win_start_cs

    parts: list[str] = []

    for i, word in enumerate(words):
        word_start_cs = round(float(word.get("start", win_start)) * 100)
        word_end_cs = round(float(word.get("end", win_start)) * 100)
        word_text = word.get("word", "").strip()

        if not word_text:
            continue

        # Pre-word silence / gap between consecutive words
        gap_cs = max(0, word_start_cs - current_cs)
        if gap_cs > 0:
            # Empty-looking span — advances the karaoke timer without visible text
            parts.append(f"{{\\k{gap_cs}}} ")

        # Word highlight duration (minimum 1 cs to avoid ASS parser issues)
        dur_cs = max(1, word_end_cs - word_start_cs)
        # Add space separator between words (omit after last word)
        space = " " if i < len(words) - 1 else ""
        parts.append(f"{{\\k{dur_cs}}}{word_text}{space}")
        current_cs = word_end_cs

    # Trailing silence — fills the remainder of the window
    trailing_cs = max(0, win_end_cs - current_cs)
    if trailing_cs > 0:
        parts.append(f"{{\\k{trailing_cs}}} ")

    return "".join(parts).strip()


def _build_ass_subtitles(
    words: list[dict],
    windows: list[tuple[float, float]],
    beat_narrations: list[str],
    output_path: str,
    aspect_ratio: str,
    width: int,
    height: int,
    font_name: str = "Inter",
    cjk_sc_font_name: str = "Noto Sans CJK SC",
    cjk_tc_font_name: str = "Noto Sans CJK TC",
    use_tc: bool = False,
) -> str:
    """
    Generate an ASS subtitle file with per-word karaoke timing.

    One subtitle ``Dialogue`` line is written per beat window.  Each line
    uses ``\\k`` tags so the spoken word is rendered in
    ``PrimaryColour`` (white) while unspoken words use ``SecondaryColour``
    (grey), producing a karaoke-style highlight effect.

    Font selection is automatic:
    - CJK Traditional (``use_tc=True``) → ``cjk_tc_font_name`` (Noto Sans CJK TC)
    - CJK Simplified                    → ``cjk_sc_font_name`` (Noto Sans CJK SC)
    - Latin / other                     → ``font_name``           (Inter)

    The font names must match the ``Family`` field embedded in the TTF files
    supplied via the ``fontsdir`` option of the FFmpeg ``subtitles=`` filter.
    Bundled fonts live in ``assets/fonts/`` and are loaded via ``_FONTS_DIR``.

    Args:
        words: Word-level alignment list from stable-whisper alignment JSON.
        windows: Per-beat ``(start_sec, end_sec)`` tuples from ScriptGuidedAligner.
        beat_narrations: Ordered narration strings, one per beat.
        output_path: Destination path for the ``.ass`` file.
        aspect_ratio: ``"9x16"`` or ``"16x9"`` — controls font size and margin.
        width: Output video width in pixels.
        height: Output video height in pixels.
        font_name: Latin font family name (embedded in Inter TTF).
        cjk_sc_font_name: Simplified Chinese font family name.
        cjk_tc_font_name: Traditional Chinese font family name.
        use_tc: If True, select the TC font for CJK content.

    Returns:
        ``output_path`` (the written ASS file).
    """
    all_text = " ".join(beat_narrations)
    use_cjk = _has_cjk_chars(all_text)
    if use_cjk:
        selected_font = cjk_tc_font_name if use_tc else cjk_sc_font_name
    else:
        selected_font = font_name

    # Portrait (9:16) uses larger text and a wider vertical margin
    is_portrait = aspect_ratio.lower().replace(":", "x") in ("9x16",)
    font_size = 68 if is_portrait else 52
    margin_v = 130 if is_portrait else 80
    margin_h = 40

    # Assign words to beats (replicates ScriptGuidedAligner token slicing)
    word_groups = _group_words_by_beats(words, beat_narrations)

    # --- ASS header ---
    header_lines: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "Collisions: Normal",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 1",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        # Style fields:
        #   PrimaryColour  &H00FFFFFF = opaque white  (active / spoken word)
        #   SecondaryColour &H00888888 = grey          (unspoken word)
        #   OutlineColour  &H00000000 = black
        #   BackColour     &HA0000000 = 63 % opaque black box
        #   BorderStyle 1 = outline + shadow
        #   Alignment 2   = bottom-center
        (
            f"Style: Default,{selected_font},{font_size},"
            "&H00FFFFFF,&H00888888,&H00000000,&HA0000000,"
            "-1,0,0,0,"
            f"100,100,0,0,"
            f"1,3,1,"
            f"2,{margin_h},{margin_h},{margin_v},1"
        ),
        "",
        "[Events]",
        (
            "Format: Layer, Start, End, Style, Name, "
            "MarginL, MarginR, MarginV, Effect, Text"
        ),
    ]

    dialogue_lines: list[str] = []
    for k, (win_start, win_end) in enumerate(windows):
        beat_words = word_groups[k] if k < len(word_groups) else []
        narration = beat_narrations[k] if k < len(beat_narrations) else ""

        if beat_words:
            text = _build_karaoke_text(beat_words, win_start, win_end)
        else:
            # Fallback: display plain narration text (no per-word timing)
            text = narration.strip()

        start_str = _sec_to_ass_time(win_start)
        end_str = _sec_to_ass_time(win_end)
        dialogue_lines.append(
            f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}"
        )

    content = "\n".join(header_lines + dialogue_lines) + "\n"
    with open(output_path, "w", encoding="utf-8-sig") as fh:
        # utf-8-sig writes a BOM which some ASS parsers expect
        fh.write(content)

    return output_path


def _mux_audio_with_subtitles(
    video_path: str,
    audio_path: str,
    ass_path: str,
    output_path: str,
    ffmpeg_path: str = "ffmpeg",
    fontsdir: str | None = None,
) -> None:
    """
    Single-pass: burn ASS subtitles into the video stream and mux narration audio.

    Combines what would otherwise be two separate FFmpeg calls
    (``_burn_subtitles`` + ``_overlay_audio``) into one encode,
    saving roughly 15–25 seconds of wall-clock render time.

    Audio is copied verbatim (strict audio invariant — no speed or
    pitch adjustments).

    Args:
        video_path: Path to the (video-only) concatenated clip.
        audio_path: Path to the narration audio file.
        ass_path: Path to the ASS subtitle file.
        output_path: Destination path for the final MP4.
        ffmpeg_path: Path to the FFmpeg binary.
        fontsdir: Directory containing bundled TTF/OTF fonts for libass.
            When set, libass will load fonts from this directory so the
            render does not depend on system-installed fonts.  Should point
            to the ``assets/fonts/`` directory (``_FONTS_DIR``).
    """
    # Use absolute path to avoid libass path resolution issues
    abs_ass = os.path.abspath(ass_path)
    # Escape backslashes and colons for the filtergraph (Windows-safe)
    escaped_ass = abs_ass.replace("\\", "/").replace(":", "\\:")

    # Build the subtitles filter string, optionally including fontsdir so
    # libass can resolve bundled fonts without system installation.
    if fontsdir:
        abs_fontsdir = os.path.abspath(fontsdir)
        escaped_fontsdir = abs_fontsdir.replace("\\", "/").replace(":", "\\:")
        subtitle_filter = (
            f"[0:v]subtitles='{escaped_ass}':fontsdir='{escaped_fontsdir}'[vout]"
        )
    else:
        subtitle_filter = f"[0:v]subtitles='{escaped_ass}'[vout]"

    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-filter_complex",
        subtitle_filter,
        "-map",
        "[vout]",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
