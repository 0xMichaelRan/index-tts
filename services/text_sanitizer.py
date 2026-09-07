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
    "\u3010\u3011"  # CJK square brackets \u2014 section/label markers
    "\uff5c"  # Full-width vertical bar \u2014 formatting separator
    "/"  # ASCII slash \u2014 ratio / path separator, never spoken
    "\u3001"  # Ideographic comma \u2014 CJK enumeration pause; TTS vocalises it
    "\uff08\uff09"  # Full-width parentheses \u2014 aside markers
    "()"  # ASCII parentheses \u2014 same reason
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
        \u3010 \u3011    CJK square brackets
        \uff5c           Full-width vertical bar
        /               ASCII slash
        \u3001           Ideographic enumeration comma
        \uff08 \uff09   Full-width parentheses
        ( )             ASCII parentheses

    Newlines and other line-break characters are preserved so that the TTS
    engine can use them as sentence boundaries.

    Args:
        text: Raw synthesis text from the job payload.

    Returns:
        Sanitized text ready for TTS inference.  Returns empty string for
        None / empty input.

    Examples:
        >>> sanitize_tts_text("\u3010\u5bfc\u8bed\u3011Hello\uff5cworld")
        '\u5bfc\u8bed Hello world'
        >>> sanitize_tts_text("\u901f\u5ea6/\u529b\u91cf\uff08power\uff09")
        '\u901f\u5ea6 \u529b\u91cf power'
        >>> sanitize_tts_text("\u82f9\u679c\u3001\u9999\u8549\u3001\u6a59\u5b50")
        '\u82f9\u679c \u9999\u8549 \u6a59\u5b50'
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
