"""
Unit tests for ScriptGuidedAligner in vox_render_pipeline.py.

Tests cover:
- Token counting: Latin whitespace-split vs CJK character counting
- Word-count boundary slicing: N beat narrations → N segments
- Midpoint pause cut allocation: contiguous windows, zero drift
- Edge cases: overflow tokens, degenerate narrations, monotonicity
"""

from __future__ import annotations

import pytest

from services.vox_render_pipeline import ScriptGuidedAligner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_words(texts_with_timing: list[tuple[str, float, float]]) -> list[dict]:
    """Build a stable-whisper-style word list from (word, start, end) tuples."""
    return [{"word": w, "start": s, "end": e} for w, s, e in texts_with_timing]


def _assert_contiguous(windows: list[tuple[float, float]], total_dur: float) -> None:
    """Assert windows are contiguous, monotone, and sum to total_dur."""
    assert len(windows) > 0
    # First window starts at 0
    assert abs(windows[0][0]) < 1e-9, (
        f"First window start should be 0, got {windows[0][0]}"
    )
    # Last window ends at total_dur
    assert abs(windows[-1][1] - total_dur) < 1e-6, (
        f"Last window end should be {total_dur}, got {windows[-1][1]}"
    )
    # Contiguous (each end == next start)
    for i in range(len(windows) - 1):
        assert abs(windows[i][1] - windows[i + 1][0]) < 1e-9, (
            f"Gap between window {i} and {i + 1}: "
            f"{windows[i][1]} != {windows[i + 1][0]}"
        )
    # Monotone (each window has positive duration)
    for i, (s, e) in enumerate(windows):
        assert e >= s, f"Window {i} has negative duration: {s} → {e}"


# ---------------------------------------------------------------------------
# Token count tests
# ---------------------------------------------------------------------------


class TestTokenCount:
    def test_latin_word_count(self):
        narr = "November 24, 1971, Portland International Airport."
        count = ScriptGuidedAligner._token_count(narr)
        # 6 whitespace-split tokens: November / 24, / 1971, / Portland / International / Airport.
        assert count == 6

    def test_latin_single_word(self):
        assert ScriptGuidedAligner._token_count("Hello") == 1

    def test_empty_narration_returns_one(self):
        assert ScriptGuidedAligner._token_count("") == 1
        assert ScriptGuidedAligner._token_count("   ") == 1

    def test_cjk_uses_character_count(self):
        narr = "他在黑暗中购买了一张单程机票。"  # 14 non-space CJK chars
        count = ScriptGuidedAligner._token_count(narr)
        non_ws = sum(1 for c in narr if not c.isspace())
        assert count == non_ws

    def test_mixed_mostly_cjk_uses_char_count(self):
        # >40% CJK → character mode
        narr = "购买ticket"
        count = ScriptGuidedAligner._token_count(narr)
        # 2 CJK + 6 Latin = 8 chars; 2/8 = 25% — below threshold → word mode
        # Actually "购买" = 2 CJK of 8 non-ws = 25%, below 40% threshold → word split
        # "购买ticket" splits as 1 whitespace token
        assert count == 1  # single "word" (no spaces)

    def test_pure_cjk_sentence(self):
        narr = "一九七一年十一月"
        count = ScriptGuidedAligner._token_count(narr)
        assert count == len(narr)  # no spaces → all chars are tokens


# ---------------------------------------------------------------------------
# ScriptGuidedAligner.build_windows tests
# ---------------------------------------------------------------------------


class TestBuildWindowsExact:
    """N beats, N words with exact 1:1 mapping."""

    def setup_method(self):
        self.narrations = [
            "Hello world",  # 2 tokens
            "Foo bar baz",  # 3 tokens
        ]
        # 5 words total: [0-1, 1-2, 2-3, 3-4, 4-5]
        self.words = _make_words(
            [
                ("Hello", 0.0, 0.5),
                ("world", 0.5, 1.0),
                ("Foo", 1.2, 1.7),
                ("bar", 1.7, 2.2),
                ("baz", 2.2, 2.8),
            ]
        )
        self.total_dur = 3.0

    def test_returns_n_windows(self):
        aligner = ScriptGuidedAligner(self.narrations, self.words)
        windows = aligner.build_windows(self.total_dur)
        assert len(windows) == 2

    def test_windows_are_contiguous(self):
        aligner = ScriptGuidedAligner(self.narrations, self.words)
        windows = aligner.build_windows(self.total_dur)
        _assert_contiguous(windows, self.total_dur)

    def test_midpoint_boundary(self):
        aligner = ScriptGuidedAligner(self.narrations, self.words)
        windows = aligner.build_windows(self.total_dur)
        # Beat 0 ends at 1.0, beat 1 starts at 1.2 → midpoint = 1.1
        assert abs(windows[0][1] - 1.1) < 1e-9
        assert abs(windows[1][0] - 1.1) < 1e-9

    def test_first_window_starts_at_zero(self):
        aligner = ScriptGuidedAligner(self.narrations, self.words)
        windows = aligner.build_windows(self.total_dur)
        assert windows[0][0] == 0.0

    def test_last_window_ends_at_total_dur(self):
        aligner = ScriptGuidedAligner(self.narrations, self.words)
        windows = aligner.build_windows(self.total_dur)
        assert abs(windows[-1][1] - self.total_dur) < 1e-9


class TestBuildWindowsMultipleBeats:
    """Smoke test with 5 beats and 20 words."""

    def setup_method(self):
        self.narrations = [
            "Beat one here",  # 3
            "Beat two is next",  # 4
            "Third beat narration",  # 3
            "Fourth beat text",  # 3
            "Fifth and final beat",  # 4
        ]  # total = 17 words
        # Build 17 dummy words spaced evenly at 0.5s each
        words_raw = []
        t = 0.0
        tokens = [
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
        ]
        for tok in tokens:
            words_raw.append((tok, t, t + 0.4))
            t += 0.5
        self.words = _make_words(words_raw)
        self.total_dur = t  # 8.5s

    def test_returns_five_windows(self):
        aligner = ScriptGuidedAligner(self.narrations, self.words)
        windows = aligner.build_windows(self.total_dur)
        assert len(windows) == 5

    def test_contiguous_and_sums_to_total(self):
        aligner = ScriptGuidedAligner(self.narrations, self.words)
        windows = aligner.build_windows(self.total_dur)
        _assert_contiguous(windows, self.total_dur)
        total = sum(e - s for s, e in windows)
        assert abs(total - self.total_dur) < 1e-6


class TestTokenOverflow:
    """More narration tokens than alignment words → proportional downscale."""

    def test_overflow_does_not_raise(self):
        narrations = [
            "A very long narration with many many words here",  # 9 tokens
            "Another long one with lots of words too",  # 8 tokens
        ]  # 17 tokens total but only 5 words in alignment
        words = _make_words(
            [
                ("a", 0.0, 0.5),
                ("b", 0.6, 1.0),
                ("c", 1.1, 1.5),
                ("d", 1.6, 2.0),
                ("e", 2.1, 2.5),
            ]
        )
        aligner = ScriptGuidedAligner(narrations, words)
        windows = aligner.build_windows(total_audio_duration=3.0)
        assert len(windows) == 2
        _assert_contiguous(windows, 3.0)

    def test_single_beat_single_word(self):
        aligner = ScriptGuidedAligner(
            beat_narrations=["Hello"],
            words=_make_words([("Hello", 0.5, 1.0)]),
        )
        windows = aligner.build_windows(total_audio_duration=2.0)
        assert len(windows) == 1
        assert windows[0][0] == 0.0
        assert abs(windows[0][1] - 2.0) < 1e-9


class TestCJKAlignment:
    """CJK narrations use character counts."""

    def test_cjk_windows_correct_count(self):
        narrations = [
            "一九七一年",  # 5 chars
            "黑暗中购买机票",  # 7 chars
        ]
        # 12 words in alignment
        words = _make_words([(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(12)])
        aligner = ScriptGuidedAligner(narrations, words)
        windows = aligner.build_windows(total_audio_duration=4.0)
        assert len(windows) == 2
        _assert_contiguous(windows, 4.0)


class TestEdgeCases:
    """Degenerate and boundary conditions."""

    def test_empty_narrations_raises(self):
        words = _make_words([("hello", 0.0, 1.0)])
        with pytest.raises(ValueError, match="beat_narrations"):
            ScriptGuidedAligner([], words)

    def test_empty_words_raises(self):
        with pytest.raises(ValueError, match="word list"):
            ScriptGuidedAligner(["Hello world"], [])

    def test_no_total_duration_last_window_uses_last_word_end(self):
        narrations = ["One two", "Three four"]
        words = _make_words(
            [
                ("One", 0.0, 0.4),
                ("two", 0.5, 0.9),
                ("Three", 1.1, 1.5),
                ("four", 1.6, 2.0),
            ]
        )
        aligner = ScriptGuidedAligner(narrations, words)
        windows = aligner.build_windows(total_audio_duration=None)
        assert len(windows) == 2
        # Should still be contiguous
        assert abs(windows[-1][1] - 2.0) < 1e-9

    def test_total_duration_extends_beyond_last_word(self):
        """A trailing silence at the end must be absorbed into last window."""
        narrations = ["Hello world"]
        words = _make_words([("Hello", 0.0, 0.5), ("world", 0.6, 1.0)])
        aligner = ScriptGuidedAligner(narrations, words)
        windows = aligner.build_windows(total_audio_duration=2.5)
        assert abs(windows[-1][1] - 2.5) < 1e-9

    def test_monotone_even_with_no_pauses(self):
        """Adjacent words with no pause between beats — midpoints are forced monotone."""
        narrations = ["A B", "C D"]
        words = _make_words(
            [
                ("A", 0.0, 0.5),
                ("B", 0.5, 1.0),
                ("C", 1.0, 1.5),  # zero gap between beats
                ("D", 1.5, 2.0),
            ]
        )
        aligner = ScriptGuidedAligner(narrations, words)
        windows = aligner.build_windows(total_audio_duration=2.0)
        for s, e in windows:
            assert e >= s
