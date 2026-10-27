"""
Text sanitization for TTS synthesis input.

Strips characters that must NOT be spoken aloud: brackets, pipes, slashes,
and other structural/formatting glyphs that IndexTTS would vocalize literally
but that carry no spoken meaning.

Context-sensitive characters are handled via a pre-pass replacement table
before the suppress regex runs:
  - Range dashes (–, ~, ～) between digits → 至  (WeText standard for ranges)
  - Em dash (—) in prose → ，
  - Residual dashes/tildes → stripped via _SUPPRESS_CHARS
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Stage 1 – Context-sensitive replacements (applied in order before stripping)
# ---------------------------------------------------------------------------
# Each entry is (compiled_pattern, replacement_string).
# Patterns run sequentially so earlier rules take priority.

_CONTEXTUAL_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    # En dash / ASCII tilde / full-width tilde used as a numeric range separator
    # e.g. "10–20人", "10~20人", "10～20人" → "10至20人"
    # Requires at least one digit on each side; optional spaces around the dash.
    (
        # U+2013 en dash, U+007E ASCII tilde, U+FF5E full-width tilde,
        # U+301C wave dash (★ standard CJK/JA range marker e.g. 10〜20人),
        # U+FF0D full-width hyphen-minus (CJK range e.g. 2020－2025),
        # U+2212 minus sign (math range e.g. 10−20)
        re.compile(r"(\d)\s*[\u2013\u007e\uff5e\u301c\uff0d\u2212]\s*(\d)"),
        r"\1至\2",
    ),
    # Em dash used as a prose continuation/parenthetical marker
    # e.g. "他很快——真的很快——到达了" → "他很快，真的很快，到达了"
    # Also matches a lone — surrounded by spaces acting as a clause break.
    (
        re.compile(r"\s*\u2014+\s*"),
        "，",
    ),
]

# ---------------------------------------------------------------------------
# Stage 2 – Hard suppression (characters with no spoken form)
# ---------------------------------------------------------------------------
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
    "\u2013"  # En dash – — residual (non-numeric context)
    "\u2015"  # Horizontal bar ―
    "\u2010"  # Hyphen ‐
    "\u2011"  # Non-breaking hyphen ‑
    "\u2012"  # Figure dash ‒
    "\u007e"  # ASCII tilde ~ — residual (non-numeric context)
    "\uff5e"  # Full-width tilde ～ — residual (non-numeric context)
    "\u301c"  # Wave dash 〜 — residual (non-numeric context; numeric handled in Stage 1)
    "\uff0d"  # Full-width hyphen-minus － — residual (non-numeric context)
    "\u2212"  # Minus sign − — residual (non-numeric context)
    "\u2e3a"  # Two-em dash ⸺ — typographic, rare
    "\u2e3b"  # Three-em dash ⸻ — typographic, rare
    "\u3030"  # Wavy dash 〰 — decorative
    "\ufe31"  # Presentation form vertical em dash ︱
    "\ufe58"  # Small em dash ﹘ (CJK compat)
    "\ufe63"  # Small hyphen-minus ﹣ (CJK compat)
    "\u00ad"  # Soft hyphen (invisible control char)
    # Note: Em dash \u2014 is intentionally omitted here; _CONTEXTUAL_REPLACEMENTS
    # converts it to ，above. Any stray double-em-dash is also caught there.
)

# Build a compiled pattern for fast repeated calls.
_SUPPRESS_RE = re.compile("[" + re.escape(_SUPPRESS_CHARS) + "]")

# Collapse runs of whitespace (including no-break space) to a single space
# so removing the above chars doesn't leave awkward double-spaces.
_WHITESPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")


def sanitize_tts_text(text: str | None) -> str:
    """
    Remove or replace characters that should not be included in TTS synthesis audio.

    Processing pipeline:
        1. Context-sensitive replacements (range dashes → 至, prose em dash → ，)
        2. Hard suppression of structural/formatting glyphs → space
        3. Whitespace collapse (runs → single space)
        4. Per-line leading/trailing strip

    Context-sensitive rules (applied first):
        N–M  N~M  N～M    Numeric range → N至M
        —                 Em dash (prose) → ，

    Characters hard-suppressed (→ space):
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
        -               ASCII hyphen/minus (bullets, residual ranges)
        \u2013          En dash – (residual, non-numeric context)
        \u2015 \u2010   Horizontal bar ―, Hyphen ‐
        \u2011 \u2012   Non-breaking hyphen ‑, Figure dash ‒
        ~ \uff5e        ASCII tilde ~, Full-width tilde ～ (residual)

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
        >>> sanitize_tts_text("10~20人")
        '10至20人'
        >>> sanitize_tts_text("2020–2025年")
        '2020至2025年'
        >>> sanitize_tts_text("他很快——真的很快——到达了")
        '他很快，真的很快，到达了'
        >>> sanitize_tts_text(None)
        ''
    """
    if not text:
        return ""

    # Step 1: Context-sensitive replacements (range dashes → 至, em dash → ，)
    cleaned = text
    for pattern, replacement in _CONTEXTUAL_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)

    # Step 2: Replace suppressed characters with a space
    cleaned = _SUPPRESS_RE.sub(" ", cleaned)

    # Step 3: Collapse resulting runs of horizontal whitespace
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)

    # Step 4: Strip leading/trailing whitespace per line
    lines = [line.strip() for line in cleaned.splitlines()]
    cleaned = "\n".join(lines)

    return cleaned.strip()
