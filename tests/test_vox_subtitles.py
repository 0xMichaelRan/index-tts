"""
Unit tests for subtitle generation and burn-in helpers in vox_render_pipeline.py.
"""

from __future__ import annotations

import os
import tempfile

from services.vox_render_pipeline import (
    _sec_to_ass_time,
    _has_cjk_chars,
    _group_words_by_beats,
    _build_karaoke_text,
    _build_ass_subtitles,
)


class TestSecToAssTime:
    def test_zero(self):
        assert _sec_to_ass_time(0.0) == "0:00:00.00"

    def test_basic_seconds(self):
        assert _sec_to_ass_time(1.5) == "0:00:01.50"

    def test_minutes(self):
        assert _sec_to_ass_time(65.25) == "0:01:05.25"

    def test_hours(self):
        assert _sec_to_ass_time(3661.05) == "1:01:01.05"

    def test_centiseconds_rounding(self):
        assert _sec_to_ass_time(0.126) == "0:00:00.13"


class TestHasCJKChars:
    def test_pure_latin(self):
        assert not _has_cjk_chars("Hello world! This is a test 123.")

    def test_chinese_simplified(self):
        assert _has_cjk_chars("你好世界")

    def test_chinese_traditional(self):
        assert _has_cjk_chars("這是一個測試")

    def test_japanese(self):
        assert _has_cjk_chars("こんにちは")

    def test_korean(self):
        assert _has_cjk_chars("안녕하세요")

    def test_mixed_latin_cjk(self):
        assert _has_cjk_chars("Hello 你好 World")


class TestGroupWordsByBeats:
    def test_equal_distribution(self):
        words = [
            {"word": f"word{i}", "start": float(i), "end": float(i) + 0.5}
            for i in range(10)
        ]
        beat_narrations = [
            "word0 word1 word2 word3 word4",
            "word5 word6 word7 word8 word9",
        ]
        groups = _group_words_by_beats(words, beat_narrations)
        assert len(groups) == 2
        assert len(groups[0]) == 5
        assert len(groups[1]) == 5

    def test_cjk_character_distribution(self):
        words = [
            {"word": char, "start": float(i), "end": float(i) + 0.5}
            for i, char in enumerate("你好世界世界和平")
        ]
        beat_narrations = ["你好世界", "世界和平"]
        groups = _group_words_by_beats(words, beat_narrations)
        assert len(groups) == 2
        assert len(groups[0]) == 4
        assert len(groups[1]) == 4

    def test_words_count_overflow_handled(self):
        words = [{"word": "one", "start": 0.0, "end": 0.5}]
        beat_narrations = ["first long sentence with many words", "second sentence"]
        groups = _group_words_by_beats(words, beat_narrations)
        assert len(groups) == 2
        total_assigned = sum(len(g) for g in groups)
        assert total_assigned <= len(words)


class TestBuildKaraokeText:
    def test_single_word_karaoke(self):
        words = [{"word": "Hello", "start": 0.0, "end": 1.0}]
        karaoke = _build_karaoke_text(words, win_start=0.0, win_end=1.5)
        # Word duration is 1.0s = 100cs; trailing padding is 0.5s = 50cs
        assert r"{\k100}Hello" in karaoke
        assert r"{\k50}" in karaoke

    def test_inter_word_gap(self):
        words = [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.8, "end": 1.2},
        ]
        karaoke = _build_karaoke_text(words, win_start=0.0, win_end=1.5)
        # Gap between 0.5s and 0.8s is 0.3s = 30cs
        assert r"{\k30}" in karaoke
        assert "Hello" in karaoke
        assert "world" in karaoke


class TestBuildAssSubtitles:
    def test_ass_file_generation(self):
        words = [
            {"word": "Hello", "start": 0.0, "end": 1.0},
            {"word": "world", "start": 1.0, "end": 2.0},
        ]
        windows = [(0.0, 2.5)]
        beat_narrations = ["Hello world"]

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "test.ass")
            _build_ass_subtitles(
                words=words,
                windows=windows,
                beat_narrations=beat_narrations,
                output_path=out_file,
                aspect_ratio="16x9",
                width=1920,
                height=1080,
            )

            assert os.path.exists(out_file)
            with open(out_file, "r", encoding="utf-8-sig") as f:
                content = f.read()

            assert "[Script Info]" in content
            assert "PlayResX: 1920" in content
            assert "PlayResY: 1080" in content
            assert "[V4+ Styles]" in content
            assert "Style: Default,Inter" in content
            assert "[Events]" in content
            assert "Dialogue: 0,0:00:00.00,0:00:02.50,Default" in content
            assert r"{\k100}Hello" in content

    def test_ass_cjk_font_selection(self):
        words = [
            {"word": "你好", "start": 0.0, "end": 1.0},
            {"word": "世界", "start": 1.0, "end": 2.0},
        ]
        windows = [(0.0, 2.0)]
        beat_narrations = ["你好世界"]

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "test_cjk.ass")
            _build_ass_subtitles(
                words=words,
                windows=windows,
                beat_narrations=beat_narrations,
                output_path=out_file,
                aspect_ratio="9x16",
                width=1080,
                height=1920,
            )

            with open(out_file, "r", encoding="utf-8-sig") as f:
                content = f.read()

            assert "Style: Default,Noto Sans CJK SC" in content
            # Portrait font size is 68
            assert ",68," in content
