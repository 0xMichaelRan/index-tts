"""Text metrics helpers for TTS analytics and duration estimation."""

from __future__ import annotations

import re

# CJK Unified Ideographs + Extension A + Compatibility Ideographs
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
# Latin words / numbers (each match = one speaking unit)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def count_words(text: str | None) -> int:
    """
    Count speaking units for bilingual ZH/EN TTS duration estimation.

    Counting rules:
    - Each CJK character counts as 1
    - Each Latin alphanumeric token counts as 1
    - Punctuation and whitespace are ignored

    Examples:
        >>> count_words("Hello world")
        2
        >>> count_words("你好世界")
        4
        >>> count_words("你好 hello")
        3
        >>> count_words("")
        0
    """
    if not text:
        return 0

    cjk_count = len(_CJK_RE.findall(text))
    latin_count = len(_LATIN_TOKEN_RE.findall(text))
    return cjk_count + latin_count
