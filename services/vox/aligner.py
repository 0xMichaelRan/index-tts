"""
ScriptGuidedAligner — derives exactly N time windows from beat narrations
and the word-level alignment output of stable-whisper.

Algorithm
---------
Step A — Word-count boundary slicing:
    For each beat narration, count its tokens (whitespace-split words for
    Latin scripts; character count for CJK scripts).  Sequentially consume
    that many words from the stable-whisper ``words`` array.  This gives
    speech_start_k / speech_end_k for each beat k without any Whisper
    segmentation heuristics.

Step B — Midpoint pause cut allocation:
    Between consecutive beats, the acoustic pause is split at its midpoint.
    Window boundaries are guaranteed monotonically non-decreasing and
    sum(window_durations) == total_audio_duration (zero drift).
"""

from __future__ import annotations

import re

from services.common.logging_config import get_logger

logger = get_logger(__name__)


class ScriptGuidedAligner:
    """
    Derives exactly N time windows from a list of beat narrations and the
    word-level alignment output of stable-whisper.

    Algorithm
    ---------
    Step A — Word-count boundary slicing:
        For each beat narration, count its tokens (whitespace-split words for
        Latin scripts; character count for CJK scripts).  Sequentially consume
        that many words from the stable-whisper ``words`` array.  This gives
        speech_start_k / speech_end_k for each beat k without any Whisper
        segmentation heuristics.

    Step B — Midpoint pause cut allocation:
        Between consecutive beats, the acoustic pause is split at its midpoint.
        Window boundaries are guaranteed monotonically non-decreasing and
        sum(window_durations) == total_audio_duration (zero drift).
    """

    # Threshold for CJK character counting vs whitespace-split word counting
    _CJK_RATIO_THRESHOLD = 0.4

    def __init__(self, beat_narrations: list[str], words: list[dict]) -> None:
        """
        Args:
            beat_narrations: Ordered list of narration strings, one per beat.
            words: Word-level alignment dicts from stable-whisper with keys
                   ``word``, ``start`` (float), ``end`` (float).

        Raises:
            ValueError: If narrations are empty or word list is empty.
        """
        if not beat_narrations:
            raise ValueError("beat_narrations must not be empty")
        if not words:
            raise ValueError("Alignment word list must not be empty")
        self.beat_narrations = beat_narrations
        self.words = words

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build_windows(
        self,
        total_audio_duration: float | None = None,
    ) -> list[tuple[float, float]]:
        """
        Return exactly ``len(beat_narrations)`` contiguous (start, end) windows.

        Args:
            total_audio_duration: Duration of the full audio file.  If provided,
                the last window extends to this value (covers any trailing silence).

        Returns:
            List of (start_sec, end_sec) tuples, length == len(beat_narrations).
        """
        n = len(self.beat_narrations)
        token_counts = [self._token_count(narr) for narr in self.beat_narrations]
        total_tokens = sum(token_counts)
        available_words = len(self.words)

        logger.debug(
            f"ScriptGuidedAligner: {n} beats, {total_tokens} tokens, "
            f"{available_words} alignment words"
        )

        if total_tokens > available_words:
            # Proportional downscale — prevents index overflow
            logger.warning(
                f"ScriptGuidedAligner: total tokens ({total_tokens}) exceeds "
                f"alignment words ({available_words}); scaling proportionally"
            )
            scale = available_words / total_tokens if total_tokens > 0 else 1.0
            min_count = 1 if available_words >= n else 0
            token_counts = [max(min_count, round(c * scale)) for c in token_counts]
            idx = n - 1
            while sum(token_counts) > available_words and idx >= 0:
                if token_counts[idx] > min_count:
                    token_counts[idx] -= 1
                else:
                    idx -= 1
            idx = n - 1
            while sum(token_counts) > available_words and idx >= 0:
                if token_counts[idx] > 0:
                    token_counts[idx] -= 1
                idx -= 1

        # Step A: Slice words per beat → {speech_start, speech_end}
        segments: list[dict] = []
        cursor = 0
        for k, count in enumerate(token_counts):
            end_cursor = min(cursor + count, available_words)
            beat_words = self.words[cursor:end_cursor]
            if not beat_words:
                # Degenerate: reuse previous segment boundary
                prev_end = segments[-1]["end"] if segments else 0.0
                segments.append({"start": prev_end, "end": prev_end})
            else:
                segments.append(
                    {
                        "start": float(beat_words[0]["start"]),
                        "end": float(beat_words[-1]["end"]),
                    }
                )
            cursor = end_cursor

        # Step B: Midpoint pause cut allocation
        return self._build_time_windows(segments, total_audio_duration)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _is_cjk(text: str) -> bool:
        """Return True if the majority of non-whitespace chars are CJK."""
        non_ws = [c for c in text if not c.isspace()]
        if not non_ws:
            return False
        cjk_count = sum(
            1
            for c in non_ws
            if "\u4e00" <= c <= "\u9fff"
            or "\u3400" <= c <= "\u4dbf"
            or "\uac00" <= c <= "\ud7a3"
            or "\u3040" <= c <= "\u30ff"
        )
        return cjk_count / len(non_ws) >= ScriptGuidedAligner._CJK_RATIO_THRESHOLD

    @staticmethod
    def _token_count(narration: str) -> int:
        """
        Count tokens in a narration string.

        Uses character count for CJK scripts (each character is a token),
        and whitespace-split word count for Latin/other scripts.
        """
        text = narration.strip()
        if not text:
            return 1
        if ScriptGuidedAligner._is_cjk(text):
            # Count non-whitespace characters for CJK
            return sum(1 for c in text if not c.isspace())
        else:
            return len(re.findall(r"\S+", text))

    @staticmethod
    def _build_time_windows(
        segments: list[dict],
        total_audio_duration: float | None,
    ) -> list[tuple[float, float]]:
        """
        Convert per-beat speech start/end pairs into fully-contiguous windows.

        The acoustic pause between consecutive beats is split at its midpoint.
        Window[0].start = 0.0, Window[-1].end = total_audio_duration (or last
        speech end if duration not provided).  This guarantees zero-gap coverage
        with sum(window_durations) == total_audio_duration.
        """
        n = len(segments)
        seg_starts = [float(s["start"]) for s in segments]
        seg_ends = [float(s["end"]) for s in segments]

        total_end = (
            max(float(total_audio_duration), seg_ends[-1])
            if total_audio_duration is not None
            else seg_ends[-1]
        )

        # Build boundary list: boundary[0] = 0.0, boundary[i] = midpoint of
        # inter-beat pause, boundary[n] = total_end
        boundaries: list[float] = [0.0]
        for i in range(n - 1):
            midpoint = (seg_ends[i] + seg_starts[i + 1]) / 2.0
            # Enforce monotonicity
            midpoint = max(boundaries[-1], midpoint)
            boundaries.append(midpoint)
        boundaries.append(max(boundaries[-1], total_end))

        return [(boundaries[i], boundaries[i + 1]) for i in range(n)]
