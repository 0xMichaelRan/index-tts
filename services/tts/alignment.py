"""
Forced Alignment Service — stable-whisper (stable-ts)

Mandatory post-synthesis step that aligns known input text to synthesised audio
and produces word-level timestamps for subtitle / karaoke / video rendering.

Design constraints (per STABLE_WHISPER_ALIGNMENT_PLAN.md §4):
  - Model: Whisper ``small``
  - Device: ``cpu`` — GPU is reserved for IndexTTS; do NOT use ``mps`` on Apple Silicon
  - Alignment runs on the **final** local_output (post time-stretch, post normalization)
  - Alignment is mandatory: failure raises an exception that fails the job
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+")
# Latin *letters* only — digits are intentionally excluded so that numerals in
# Chinese text (e.g. "1949年") are not counted as Latin script characters.
_LATIN_LETTERS_RE = re.compile(r"[A-Za-z]+")

# Characters that suggest normalization mismatch risk (digits, currency, etc.)
_NORMALIZATION_RISK_RE = re.compile(r"[$\u20ac\xa3\xa5%]|\d")

# Mapping of TTS locale tags (lowercased) → Whisper 2-letter ISO-639-1 codes.
# Add entries here when new locales are needed; unknown hints fall through to
# heuristic script detection.
_WHISPER_LANG_MAP: dict[str, str] = {
    # Chinese variants
    "zh": "zh",
    "zh-cn": "zh",
    "zh-tw": "zh",
    "zh-hk": "zh",
    "zh-sg": "zh",
    # English variants
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "en-au": "en",
    # Other languages supported by Whisper small
    "ja": "ja",
    "ko": "ko",
    "fr": "fr",
    "de": "de",
    "es": "es",
    "pt": "pt",
    "ru": "ru",
    "it": "it",
}


# ---------------------------------------------------------------------------
# Language-strategy helpers (v2: hint-priority)
# ---------------------------------------------------------------------------


def normalize_whisper_lang(hint: str | None) -> str | None:
    """
    Normalize a TTS locale tag to a Whisper-compatible 2-letter ISO-639-1 code.

    Examples::

        normalize_whisper_lang("zh-CN")  # → "zh"
        normalize_whisper_lang("zh-TW")  # → "zh"
        normalize_whisper_lang("en-US")  # → "en"
        normalize_whisper_lang("en")     # → "en"
        normalize_whisper_lang(None)     # → None
        normalize_whisper_lang("xx-YY") # → None (unknown; triggers heuristic)

    Returns ``None`` when the hint is absent or unrecognized so callers can
    fall back to heuristic script detection.
    """
    if not hint:
        return None
    normalized = _WHISPER_LANG_MAP.get(hint.lower())
    if normalized:
        return normalized
    # Try base subtag only (e.g. "zh" from "zh-Hant-HK")
    base = hint.split("-")[0].split("_")[0].lower()
    return _WHISPER_LANG_MAP.get(base)


def detect_language_strategy(text: str, language_hint: str | None) -> tuple[str, str]:
    """
    Determine the alignment language and quality tag for stable-whisper.

    v2 strategy (hint-priority):

    1. **Hint provided and recognized** → always use the TTS job's original
       locale as the Whisper language; quality tag is ``locale_<lang>``
       (e.g. ``locale_zh``, ``locale_en``).
       This is the common case: the synthesiser already knows the language.

    2. **No recognized hint** → heuristic script analysis:

       - No CJK characters → ``"en"``; quality ``"monolingual_en"``
       - No Latin *letters* (digits excluded) → ``"zh"``; quality ``"monolingual_zh"``
       - Mixed → majority-letter rule (Latin letters > 40 % of letter+CJK
         count → ``"en"``); quality ``"mixed_fallback"``

    .. note::
        Digits (0–9) are intentionally **not** treated as Latin script.
        Text like ``"1949年到2024年"`` is pure Chinese narration and should
        produce ``"monolingual_zh"``, not ``"mixed_fallback"``.

    Args:
        text:           The TTS source text (after sanitization).
        language_hint:  BCP-47 locale tag from the RabbitMQ job payload
                        (``spokenLang``), e.g. ``"zh-CN"``, ``"zh-TW"``,
                        ``"en"``. May be ``None``.

    Returns:
        ``(whisper_language, alignment_quality)``
    """
    # --- v2 path: honour the TTS job locale ---
    whisper_lang = normalize_whisper_lang(language_hint)
    if whisper_lang is not None:
        return whisper_lang, f"locale_{whisper_lang}"

    # --- Heuristic fallback: no usable hint ---
    # Count CJK characters and Latin *letters* (digits excluded).
    cjk_chars = sum(len(m.group()) for m in _CJK_RE.finditer(text))
    latin_letter_chars = sum(len(m.group()) for m in _LATIN_LETTERS_RE.finditer(text))
    total_script = cjk_chars + latin_letter_chars

    if total_script == 0:
        # No letters at all (punctuation / digits only) — assume Chinese
        return "zh", "monolingual_zh"

    if cjk_chars == 0:
        return "en", "monolingual_en"

    if latin_letter_chars == 0:
        return "zh", "monolingual_zh"

    # Mixed script — majority-letter rule
    latin_ratio = latin_letter_chars / total_script
    lang = "en" if latin_ratio > 0.40 else "zh"
    return lang, "mixed_fallback"


def has_normalization_risk(text: str) -> bool:
    """Return True if text contains tokens likely to be expanded by TextNormalizer."""
    return bool(_NORMALIZATION_RISK_RE.search(text))


# ---------------------------------------------------------------------------
# AlignmentResult
# ---------------------------------------------------------------------------


class AlignmentResult:
    """Parsed result from a single alignment pass."""

    __slots__ = (
        "job_id",
        "whisper_language",
        "alignment_quality",
        "audio_duration_seconds",
        "source_text",
        "segments",
        "words",
        "alignment_duration_seconds",
        "aligned_at",
        "engine_version",
    )

    def __init__(
        self,
        *,
        job_id: str,
        whisper_language: str,
        alignment_quality: str,
        audio_duration_seconds: float,
        source_text: str,
        segments: list[dict[str, Any]],
        words: list[dict[str, Any]],
        alignment_duration_seconds: float,
        aligned_at: str,
        engine_version: str,
    ) -> None:
        self.job_id = job_id
        self.whisper_language = whisper_language
        self.alignment_quality = alignment_quality
        self.audio_duration_seconds = audio_duration_seconds
        self.source_text = source_text
        self.segments = segments
        self.words = words
        self.alignment_duration_seconds = alignment_duration_seconds
        self.aligned_at = aligned_at
        self.engine_version = engine_version

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the v2 parsed JSON schema."""
        return {
            "version": "1.0",
            "job_id": self.job_id,
            "engine": "stable-whisper",
            "engine_version": self.engine_version,
            "model": "small",
            "device": "cpu",
            "audio_duration_seconds": round(self.audio_duration_seconds, 4),
            # whisper_language: the 2-letter ISO-639-1 code passed to Whisper
            # (e.g. "zh", "en"). Consumers use this for language-aware rendering.
            "whisper_language": self.whisper_language,
            "language_strategy": self.alignment_quality,
            "alignment_quality": self.alignment_quality,
            "source_text": self.source_text,
            "segments": self.segments,
            "words": self.words,
            "alignment_duration_seconds": round(self.alignment_duration_seconds, 4),
            "aligned_at": self.aligned_at,
        }


# ---------------------------------------------------------------------------
# AlignmentService
# ---------------------------------------------------------------------------


class AlignmentService:
    """
    Singleton wrapper around stable-whisper for forced alignment.

    Instantiate once in the worker ``__init__``, then call :meth:`align_to_files`
    for each job after time-stretching and before S3 upload.

    Example::

        service = AlignmentService()
        service.load_model()          # amortise load cost at startup
        raw_json, srt, parsed_json = service.align_to_files(
            job_id="abc123",
            audio_path="/outputs/tts_output/abc123/abc123_ts.wav",
            text="你好，世界。",
            language_hint="zh",
            output_dir="/outputs/tts_output/abc123",
        )
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        download_root: str | None = None,
    ) -> None:
        self._model_name: str = model_name or os.getenv("TTS_ALIGNMENT_MODEL", "small")
        self._device: str = device or os.getenv("TTS_ALIGNMENT_DEVICE", "cpu")
        self._download_root: str | None = download_root or os.getenv(
            "TTS_ALIGNMENT_MODEL_DIR"
        )

        # Validate device — never allow mps (Apple Silicon float64 crash, plan §4 / §15)
        if self._device == "mps":
            logger.warning(
                "TTS_ALIGNMENT_DEVICE=mps is not supported (float64 crash on Apple Silicon). "
                "Overriding to cpu."
            )
            self._device = "cpu"

        self._engine_version: str = self._get_stable_ts_version()
        self._model = None
        self._model_loaded = False
        self._last_raw_result = (
            None  # stored so align_to_files can write SRT / raw JSON
        )

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _get_stable_ts_version(self) -> str:
        try:
            import stable_whisper  # noqa: PLC0415

            return getattr(stable_whisper, "__version__", "unknown")
        except ImportError:
            return "unknown"

    def load_model(self) -> None:
        """
        Load the Whisper model.

        Called from worker ``__init__`` so the ~2–5 s load cost is amortised
        across jobs rather than paid on the first request.
        """
        if self._model_loaded:
            return

        import ssl
        import platform as _platform
        import stable_whisper  # noqa: PLC0415

        load_kwargs: dict[str, Any] = {
            "name": self._model_name,
            "device": self._device,
        }
        if self._download_root:
            load_kwargs["download_root"] = self._download_root

        logger.info(
            "Loading stable-whisper model '%s' on %s …",
            self._model_name,
            self._device,
        )

        # Windows SSL fix: bypass certificate verification for model downloads
        # This prevents "[ASN1: NOT_ENOUGH_DATA] not enough data" errors
        if _platform.system() == "Windows":
            logger.info("Windows detected: configuring SSL context for model download")
            ssl._create_default_https_context = ssl._create_unverified_context

        t0 = time.time()
        try:
            self._model = stable_whisper.load_model(**load_kwargs)
            elapsed = time.time() - t0
            self._model_loaded = True
            logger.info(
                "stable-whisper '%s' loaded on %s in %.1f s (version: %s)",
                self._model_name,
                self._device,
                elapsed,
                self._engine_version,
            )
        finally:
            # Restore default SSL context after model loading
            if _platform.system() == "Windows":
                ssl._create_default_https_context = ssl.create_default_context

    # ------------------------------------------------------------------
    # Core alignment
    # ------------------------------------------------------------------

    def align(
        self,
        audio_path: str,
        text: str,
        language_hint: str | None = None,
        job_id: str = "unknown",
    ) -> AlignmentResult:
        """
        Run forced alignment of ``text`` against ``audio_path``.

        Args:
            audio_path:     Absolute (or relative) path to the final WAV file.
            text:           Original job text (raw, as received from RabbitMQ).
            language_hint:  Language code from the job (e.g. ``"zh"``, ``"en"``).
            job_id:         Job identifier for log prefixing.

        Returns:
            :class:`AlignmentResult` with parsed word/segment timestamps.

        Raises:
            FileNotFoundError: If ``audio_path`` does not exist.
            ValueError:        If ``text`` is empty or whitespace-only.
            RuntimeError:      If stable-whisper alignment fails.
        """
        if not text or not text.strip():
            raise ValueError(
                f"[JOB {job_id}] ALIGNMENT_INVALID_INPUT: text is empty or whitespace"
            )

        if not os.path.exists(audio_path):
            raise FileNotFoundError(
                f"[JOB {job_id}] ALIGNMENT_AUDIO_NOT_FOUND: {audio_path}"
            )

        if not self._model_loaded:
            self.load_model()

        whisper_language, alignment_quality = detect_language_strategy(
            text, language_hint
        )

        if has_normalization_risk(text):
            logger.warning(
                "[JOB %s] Text contains digits/currency/symbols — forced alignment may "
                "diverge from spoken form (normalization mismatch risk). "
                "alignment_quality: %s",
                job_id,
                alignment_quality,
            )

        logger.info(
            "[JOB %s] Running forced alignment (model=%s, device=%s, lang=%s, strategy=%s)…",
            job_id,
            self._model_name,
            self._device,
            whisper_language,
            alignment_quality,
        )

        t_align_start = time.time()
        try:
            raw_result = self._model.align(
                audio=audio_path,
                text=text,
                language=whisper_language,
            )
        except Exception as exc:
            raise RuntimeError(
                f"[JOB {job_id}] ALIGNMENT_FAILED: stable-whisper raised: {exc}"
            ) from exc

        alignment_duration = time.time() - t_align_start

        result = self._parse_result(
            raw_result=raw_result,
            job_id=job_id,
            audio_path=audio_path,
            text=text,
            whisper_language=whisper_language,
            alignment_quality=alignment_quality,
            alignment_duration=alignment_duration,
        )

        logger.info(
            "[JOB %s] Alignment complete: %d words, audio=%.1fs, align took %.1fs",
            job_id,
            len(result.words),
            result.audio_duration_seconds,
            alignment_duration,
        )
        return result

    # ------------------------------------------------------------------
    # Convenience: align and write all three output artefacts
    # ------------------------------------------------------------------

    def align_to_files(
        self,
        job_id: str,
        audio_path: str,
        text: str,
        language_hint: str | None,
        output_dir: str,
    ) -> tuple[str, str, str]:
        """
        Run alignment and write the three output artefacts (plan §7).

        File lifecycle:
          - ``{job_id}_raw_alignment.json``  → retained on disk; NOT uploaded to S3
          - ``{job_id}_alignment.srt``       → retained on disk; NOT uploaded to S3
          - ``{job_id}_alignment.json``      → uploaded to S3, then deleted locally

        Returns:
            ``(raw_json_path, srt_path, parsed_json_path)``
        """
        os.makedirs(output_dir, exist_ok=True)

        result = self.align(
            audio_path=audio_path,
            text=text,
            language_hint=language_hint,
            job_id=job_id,
        )

        raw_result = self._last_raw_result

        raw_json_path = os.path.join(output_dir, f"{job_id}_raw_alignment.json")
        srt_path = os.path.join(output_dir, f"{job_id}_alignment.srt")
        parsed_json_path = os.path.join(output_dir, f"{job_id}_alignment.json")

        # Write raw stable-whisper JSON (retained on disk)
        if raw_result is not None:
            try:
                raw_dict = raw_result.to_dict()
                with open(raw_json_path, "w", encoding="utf-8") as fh:
                    json.dump(raw_dict, fh, ensure_ascii=False, indent=2)
            except Exception as exc:
                logger.warning(
                    "[JOB %s] Failed to write raw alignment JSON: %s", job_id, exc
                )

        # Write SRT (retained on disk)
        if raw_result is not None:
            try:
                raw_result.to_srt_vtt(srt_path, word_level=True)
            except Exception as exc:
                logger.warning(
                    "[JOB %s] Failed to write alignment SRT: %s", job_id, exc
                )

        # Write parsed JSON (uploaded to S3 then deleted)
        with open(parsed_json_path, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, ensure_ascii=False, indent=2)

        return raw_json_path, srt_path, parsed_json_path

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_result(
        self,
        *,
        raw_result: Any,
        job_id: str,
        audio_path: str,
        text: str,
        whisper_language: str,
        alignment_quality: str,
        alignment_duration: float,
    ) -> AlignmentResult:
        """Convert a stable-whisper result into our JSON schema v1."""
        self._last_raw_result = raw_result

        audio_duration = self._get_audio_duration(audio_path)

        segments: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []

        for seg_idx, seg in enumerate(getattr(raw_result, "segments", [])):
            seg_words: list[dict[str, Any]] = []
            for w in getattr(seg, "words", []):
                word_entry = {
                    "word": getattr(w, "word", ""),
                    "start": round(float(getattr(w, "start", 0.0)), 4),
                    "end": round(float(getattr(w, "end", 0.0)), 4),
                    "probability": round(float(getattr(w, "probability", 0.0)), 4),
                }
                seg_words.append(word_entry)
                words.append(word_entry)

            segments.append(
                {
                    "id": seg_idx,
                    "text": getattr(seg, "text", ""),
                    "language": whisper_language,
                    "start": round(float(getattr(seg, "start", 0.0)), 4),
                    "end": round(float(getattr(seg, "end", 0.0)), 4),
                    "words": seg_words,
                }
            )

        aligned_at = datetime.now(tz=timezone.utc).isoformat()

        return AlignmentResult(
            job_id=job_id,
            whisper_language=whisper_language,
            alignment_quality=alignment_quality,
            audio_duration_seconds=audio_duration,
            source_text=text,
            segments=segments,
            words=words,
            alignment_duration_seconds=alignment_duration,
            aligned_at=aligned_at,
            engine_version=self._engine_version,
        )

    @staticmethod
    def _get_audio_duration(audio_path: str) -> float:
        """Return duration in seconds via wave module; rough fallback on error."""
        import wave as _wave  # noqa: PLC0415

        try:
            with _wave.open(audio_path, "r") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except Exception:
            return os.path.getsize(audio_path) / 48000.0
