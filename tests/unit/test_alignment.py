"""
Unit tests for services/alignment.py

Tests language strategy detection, JSON schema round-trip,
and AlignmentService helper behaviour without loading the Whisper model.
"""

from __future__ import annotations

import json
import os
import tempfile
import wave

import pytest

from services.tts.alignment import (
    AlignmentResult,
    AlignmentService,
    detect_language_strategy,
    has_normalization_risk,
    normalize_whisper_lang,
)


# ---------------------------------------------------------------------------
# detect_language_strategy
# ---------------------------------------------------------------------------


class TestDetectLanguageStrategy:
    """Tests for the v2 language strategy detection (hint-priority)."""

    # ------------------------------------------------------------------
    # v2: hint-provided cases (always follow the TTS locale)
    # ------------------------------------------------------------------

    def test_zh_hint_returns_locale_zh(self):
        lang, quality = detect_language_strategy("你好世界", "zh")
        assert lang == "zh"
        assert quality == "locale_zh"

    def test_zh_cn_hint_returns_locale_zh(self):
        lang, quality = detect_language_strategy("你好世界", "zh-CN")
        assert lang == "zh"
        assert quality == "locale_zh"

    def test_zh_tw_hint_returns_locale_zh(self):
        lang, quality = detect_language_strategy("你好世界", "zh-TW")
        assert lang == "zh"
        assert quality == "locale_zh"

    def test_en_hint_returns_locale_en(self):
        lang, quality = detect_language_strategy("Hello world", "en")
        assert lang == "en"
        assert quality == "locale_en"

    def test_en_us_hint_returns_locale_en(self):
        lang, quality = detect_language_strategy("Hello world", "en-US")
        assert lang == "en"
        assert quality == "locale_en"

    def test_zh_hint_with_mixed_text_still_locale_zh(self):
        """Even mixed text respects the hint — locale priority wins."""
        lang, quality = detect_language_strategy("AI力量无穷", "zh-CN")
        assert lang == "zh"
        assert quality == "locale_zh"

    def test_en_hint_with_chinese_text_respects_hint(self):
        """TTS job said en; alignment must follow the job, not the text glyphs."""
        lang, quality = detect_language_strategy("你好", "en")
        assert lang == "en"
        assert quality == "locale_en"

    def test_zh_hint_with_numbers_locale_zh(self):
        """Digits in Chinese narration must not override the hint."""
        lang, quality = detect_language_strategy("1949年到2024年", "zh-CN")
        assert lang == "zh"
        assert quality == "locale_zh"

    # ------------------------------------------------------------------
    # v2: no-hint heuristic cases
    # ------------------------------------------------------------------

    def test_pure_chinese_no_hint(self):
        lang, quality = detect_language_strategy("你好世界", None)
        assert lang == "zh"
        assert quality == "monolingual_zh"

    def test_pure_english_no_hint(self):
        lang, quality = detect_language_strategy("Hello world", None)
        assert lang == "en"
        assert quality == "monolingual_en"

    def test_chinese_with_numbers_no_hint_is_monolingual_zh(self):
        """Digits must NOT be counted as Latin letters (v2 digit-fix)."""
        lang, quality = detect_language_strategy("1949年到2024年", None)
        assert lang == "zh"
        assert quality == "monolingual_zh"

    def test_mixed_latin_majority_no_hint(self):
        # "hello world" = 10 Latin letters, "好" = 1 CJK → 91 % Latin → en
        lang, quality = detect_language_strategy("hello world 好", None)
        assert lang == "en"
        assert quality == "mixed_fallback"

    def test_mixed_cjk_majority_no_hint(self):
        # "你好世界hi" = 4 CJK, 2 Latin letters → 33 % Latin → zh
        lang, quality = detect_language_strategy("你好世界hi", None)
        assert lang == "zh"
        assert quality == "mixed_fallback"

    def test_mixed_exactly_40_percent_latin_uses_zh(self):
        # "abcd你好世界xy" → 6 Latin letters, 4 CJK → 60 % Latin → en
        lang, quality = detect_language_strategy("abcd你好世界xy", None)
        assert lang == "en"
        assert quality == "mixed_fallback"

    def test_no_alphanumeric_no_hint_defaults_zh(self):
        lang, quality = detect_language_strategy("，。！", None)
        assert lang == "zh"
        assert quality == "monolingual_zh"

    def test_digits_only_no_hint_defaults_zh(self):
        """Pure digit text has no letters; should default to zh."""
        lang, quality = detect_language_strategy("1234567890", None)
        assert lang == "zh"
        assert quality == "monolingual_zh"

    def test_unknown_hint_falls_through_to_heuristic(self):
        """Unrecognized locale tags trigger heuristic detection."""
        lang, quality = detect_language_strategy("你好世界", "xx-YY")
        assert lang == "zh"
        assert quality == "monolingual_zh"

    def test_english_with_punctuation_only(self):
        lang, quality = detect_language_strategy("Hello, world!", None)
        assert lang == "en"
        assert quality == "monolingual_en"


# ---------------------------------------------------------------------------
# normalize_whisper_lang
# ---------------------------------------------------------------------------


class TestNormalizeWhisperLang:
    """Tests for BCP-47 locale tag → Whisper 2-letter code normalization."""

    def test_zh_returns_zh(self):
        assert normalize_whisper_lang("zh") == "zh"

    def test_zh_cn_returns_zh(self):
        assert normalize_whisper_lang("zh-CN") == "zh"

    def test_zh_tw_returns_zh(self):
        assert normalize_whisper_lang("zh-TW") == "zh"

    def test_zh_hk_returns_zh(self):
        assert normalize_whisper_lang("zh-HK") == "zh"

    def test_en_returns_en(self):
        assert normalize_whisper_lang("en") == "en"

    def test_en_us_returns_en(self):
        assert normalize_whisper_lang("en-US") == "en"

    def test_en_gb_returns_en(self):
        assert normalize_whisper_lang("en-GB") == "en"

    def test_none_returns_none(self):
        assert normalize_whisper_lang(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_whisper_lang("") is None

    def test_unknown_locale_returns_none(self):
        assert normalize_whisper_lang("xx-YY") is None

    def test_base_subtag_fallback(self):
        """zh-Hant-HK → base subtag 'zh' → 'zh'."""
        assert normalize_whisper_lang("zh-Hant-HK") == "zh"

    def test_case_insensitive(self):
        assert normalize_whisper_lang("ZH-CN") == "zh"
        assert normalize_whisper_lang("EN-US") == "en"


# ---------------------------------------------------------------------------
# has_normalization_risk
# ---------------------------------------------------------------------------


class TestHasNormalizationRisk:
    """Tests for normalization risk detection."""

    def test_digits_detected(self):
        assert has_normalization_risk("第3章") is True

    def test_currency_dollar_detected(self):
        assert has_normalization_risk("$50") is True

    def test_percent_detected(self):
        assert has_normalization_risk("50%") is True

    def test_clean_chinese_no_risk(self):
        assert has_normalization_risk("你好世界") is False

    def test_clean_english_no_risk(self):
        assert has_normalization_risk("Hello world") is False


# ---------------------------------------------------------------------------
# AlignmentResult JSON schema
# ---------------------------------------------------------------------------


class TestAlignmentResultSchema:
    """Tests for the v1 JSON schema round-trip."""

    def _make_result(self, **overrides) -> AlignmentResult:
        defaults = dict(
            job_id="test_job_001",
            whisper_language="zh",
            alignment_quality="monolingual_zh",
            audio_duration_seconds=12.34,
            source_text="你好，世界。",
            segments=[
                {
                    "id": 0,
                    "text": "你好，世界。",
                    "language": "zh",
                    "start": 0.0,
                    "end": 12.34,
                    "words": [
                        {"word": "你", "start": 0.12, "end": 0.28, "probability": 0.98},
                        {"word": "好", "start": 0.28, "end": 0.52, "probability": 0.97},
                    ],
                }
            ],
            words=[
                {"word": "你", "start": 0.12, "end": 0.28, "probability": 0.98},
                {"word": "好", "start": 0.28, "end": 0.52, "probability": 0.97},
            ],
            alignment_duration_seconds=1.87,
            aligned_at="2026-09-01T02:30:00+00:00",
            engine_version="2.19.1",
        )
        defaults.update(overrides)
        return AlignmentResult(**defaults)

    def test_to_dict_has_required_fields(self):
        result = self._make_result()
        d = result.to_dict()

        required = {
            "version",
            "job_id",
            "engine",
            "engine_version",
            "model",
            "device",
            "audio_duration_seconds",
            "whisper_language",
            "language_strategy",
            "alignment_quality",
            "source_text",
            "segments",
            "words",
            "alignment_duration_seconds",
            "aligned_at",
        }
        assert required.issubset(d.keys()), f"Missing keys: {required - d.keys()}"

    def test_to_dict_version_is_1_0(self):
        assert self._make_result().to_dict()["version"] == "1.0"

    def test_to_dict_engine_is_stable_whisper(self):
        assert self._make_result().to_dict()["engine"] == "stable-whisper"

    def test_to_dict_model_is_small(self):
        assert self._make_result().to_dict()["model"] == "small"

    def test_to_dict_device_is_cpu(self):
        assert self._make_result().to_dict()["device"] == "cpu"

    def test_json_round_trip(self):
        result = self._make_result()
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        restored = json.loads(serialized)

        assert restored["job_id"] == "test_job_001"
        assert restored["source_text"] == "你好，世界。"
        assert len(restored["segments"]) == 1
        assert len(restored["words"]) == 2

    def test_audio_duration_rounded(self):
        result = self._make_result(audio_duration_seconds=12.3456789)
        d = result.to_dict()
        # Should be rounded to 4 decimal places
        assert d["audio_duration_seconds"] == round(12.3456789, 4)

    def test_alignment_quality_values(self):
        for quality in (
            "monolingual_zh",
            "monolingual_en",
            "mixed_fallback",
            "locale_zh",
            "locale_en",
        ):
            r = self._make_result(alignment_quality=quality)
            d = r.to_dict()
            assert d["alignment_quality"] == quality
            assert d["language_strategy"] == quality  # both must match

    def test_whisper_language_in_dict(self):
        r = self._make_result(whisper_language="zh")
        assert r.to_dict()["whisper_language"] == "zh"


# ---------------------------------------------------------------------------
# AlignmentService — validation logic (no model loaded)
# ---------------------------------------------------------------------------


class TestAlignmentServiceValidation:
    """Tests that validation errors are raised without loading the model."""

    def setup_method(self):
        # Create service but skip model loading
        self.service = AlignmentService.__new__(AlignmentService)
        self.service._model_name = "small"
        self.service._device = "cpu"
        self.service._download_root = None
        self.service._engine_version = "2.19.1"
        self.service._model = None
        self.service._model_loaded = False
        self.service._last_raw_result = None

    def test_empty_text_raises_value_error(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        try:
            with pytest.raises(ValueError, match="ALIGNMENT_INVALID_INPUT"):
                self.service.align(audio_path=tmp, text="", job_id="j1")
        finally:
            os.unlink(tmp)

    def test_whitespace_text_raises_value_error(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        try:
            with pytest.raises(ValueError, match="ALIGNMENT_INVALID_INPUT"):
                self.service.align(audio_path=tmp, text="   \n\t  ", job_id="j1")
        finally:
            os.unlink(tmp)

    def test_missing_audio_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="ALIGNMENT_AUDIO_NOT_FOUND"):
            self.service.align(
                audio_path="/nonexistent/path/audio.wav",
                text="Hello",
                job_id="j1",
            )

    def test_mps_device_overridden_to_cpu(self):
        svc = AlignmentService(device="mps")
        assert svc._device == "cpu"

    def test_default_model_is_small(self):
        svc = AlignmentService()
        assert svc._model_name == "small"

    def test_default_device_is_cpu(self):
        svc = AlignmentService()
        assert svc._device == "cpu"


# ---------------------------------------------------------------------------
# AlignmentService._get_audio_duration (static helper)
# ---------------------------------------------------------------------------


class TestGetAudioDuration:
    """Tests for the WAV-duration helper used inside _parse_result."""

    def _make_wav(self, duration_s: float, sample_rate: int = 24000) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        n_frames = int(duration_s * sample_rate)
        with wave.open(tmp.name, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * n_frames)
        return tmp.name

    def test_duration_correct_for_wav(self):
        wav = self._make_wav(5.0)
        try:
            dur = AlignmentService._get_audio_duration(wav)
            assert abs(dur - 5.0) < 0.01
        finally:
            os.unlink(wav)

    def test_duration_various_sample_rates(self):
        for sr in (16000, 22050, 24000, 44100):
            wav = self._make_wav(3.0, sample_rate=sr)
            try:
                assert abs(AlignmentService._get_audio_duration(wav) - 3.0) < 0.01
            finally:
                os.unlink(wav)
