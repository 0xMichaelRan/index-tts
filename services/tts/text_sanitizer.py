"""
Text sanitization for TTS synthesis input.

Strips characters that must NOT be spoken aloud: brackets, pipes, slashes,
and other structural/formatting glyphs that IndexTTS would vocalize literally
but that carry no spoken meaning.
"""

from __future__ import annotations

import re

# Characters to remove entirely before synthesis.
# Each entry is documented with the reason it must be suppressed.
_SUPPRESS_CHARS: str = (
    "\u3010\u3011"  # CJK square brackets 【 】 — section/label markers
    "\uff5c"  # Full-width vertical bar ｜ — formatting separator
    "/"  # ASCII slash — ratio / path separator, never spoken
    "\u3001"  # Ideographic comma 、 — CJK enumeration pause; TTS vocalises it
    "\uff08\uff09"  # Full-width parentheses （ ） — aside markers
    "()"  # ASCII parentheses ( ) — same reason
    "\u300a\u300b"  # CJK double angle brackets 《 》 — book/title marks vocalized by TTS
    "\u3008\u3009"  # CJK single angle brackets 〈 〉 — title/quote markers
    "\u300c\u300d"  # CJK corner brackets 「 」 — quote markers
    "\u300e\u300f"  # CJK white corner brackets 『 』 — quote markers
    "\u3014\u3015"  # CJK tortoise shell brackets 〔 〕
    "\u3016\u3017"  # CJK white lenticular brackets 〖 〗
    "-"  # ASCII hyphen/minus — bullet/range/break vocalized as "hyphen"/"minus"/"减"
    "\u2014"  # Em dash — — long pause marker vocalized as dash
    "\u2013"  # En dash – — range/dash marker
    "\u2015"  # Horizontal bar ―
    "\u2010"  # Hyphen ‐
    "\u2011"  # Non-breaking hyphen ‑
    "\u2012"  # Figure dash ‒
    "\u007e"  # ASCII tilde ~ — wave dash vocalized or causing artifacts
    "\uff5e"  # Full-width tilde ～
)

# Build a compiled pattern for fast repeated calls.
_SUPPRESS_RE = re.compile("[" + re.escape(_SUPPRESS_CHARS) + "]")

# Collapse runs of whitespace (including no-break space) to a single space
# so removing the above chars doesn't leave awkward double-spaces.
_WHITESPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")


def sanitize_tts_text(text: str | None) -> str:
    """
    Remove characters that should not be included in TTS synthesis audio.

    Characters suppressed:
        \u3010 \u3011    CJK square brackets 【 】
        \uff5c           Full-width vertical bar ｜
        /               ASCII slash
        \u3001           Ideographic enumeration comma 、
        \uff08 \uff09   Full-width parentheses （ ）
        ( )             ASCII parentheses
        \u300a \u300b    CJK double angle brackets 《 》 (book title marks)
        \u3008 \u3009    CJK single angle brackets 〈 〉
        \u300c \u300d    CJK corner brackets 「 」
        \u300e \u300f    CJK white corner brackets 『 』
        \u3014 \u3015    CJK tortoise shell brackets 〔 〕
        \u3016 \u3017    CJK white lenticular brackets 〖 〗
        -               ASCII hyphen/minus
        \u2014 \u2013   Em dash —, En dash –
        \u2015 \u2010   Horizontal bar ―, Hyphen ‐, Non-breaking hyphen ‑, Figure dash ‒
        ~ \uff5e        ASCII tilde ~, Full-width tilde ～

    Newlines and other line-break characters are preserved so that the TTS
    engine can use them as sentence boundaries.

    Args:
        text: Raw synthesis text from the job payload.

    Returns:
        Sanitized text ready for TTS inference. Returns empty string for
        None / empty input.

    Examples:
        >>> sanitize_tts_text("【导语】Hello｜world")
        '导语 Hello world'
        >>> sanitize_tts_text("速度/力量（power）")
        '速度 力量 power'
        >>> sanitize_tts_text("《三体》- 刘慈欣")
        '三体 刘慈欣'
        >>> sanitize_tts_text("苹果、香蕉、橙子")
        '苹果 香蕉 橙子'
        >>> sanitize_tts_text(None)
        ''
    """
    if not text:
        return ""

    # Step 1: Replace suppressed characters with a space
    cleaned = _SUPPRESS_RE.sub(" ", text)

    # Step 2: Collapse resulting runs of horizontal whitespace
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)

    # Step 3: Strip leading/trailing whitespace per line
    lines = [line.strip() for line in cleaned.splitlines()]
    cleaned = "\n".join(lines)

    return cleaned.strip()
