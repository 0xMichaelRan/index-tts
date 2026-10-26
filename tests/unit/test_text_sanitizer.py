"""Unit tests for TTS text sanitization."""

from services.tts.text_sanitizer import sanitize_tts_text


class TestSanitizeTtsText:
    """Test suite for sanitize_tts_text functionality."""

    def test_book_title_marks(self):
        """Verify 《 and 》 (book title marks) are removed."""
        assert sanitize_tts_text("推荐阅读《三体》全集") == "推荐阅读 三体 全集"
        assert sanitize_tts_text("《百年孤独》") == "百年孤独"
        assert sanitize_tts_text("《A》与《B》") == "A 与 B"

    def test_single_angle_brackets(self):
        """Verify 〈 and 〉 are removed."""
        assert sanitize_tts_text("篇章〈序言〉介绍") == "篇章 序言 介绍"

    def test_hyphen_and_minus(self):
        """Verify hyphen/minus characters are removed."""
        assert sanitize_tts_text("第一点 - 第二点") == "第一点 第二点"
        assert sanitize_tts_text("item-one") == "item one"
        assert sanitize_tts_text("《三体》- 刘慈欣") == "三体 刘慈欣"

    def test_em_dash_and_en_dash(self):
        """Verify em-dash, en-dash, and horizontal bars are removed."""
        assert sanitize_tts_text("思考——这是一次探索") == "思考 这是一次探索"
        assert sanitize_tts_text("2020–2025") == "2020 2025"
        assert sanitize_tts_text("标题―副标题") == "标题 副标题"

    def test_tilde_and_wave_dash(self):
        """Verify ASCII and full-width tildes are removed."""
        assert sanitize_tts_text("大约10~20人") == "大约10 20人"
        assert sanitize_tts_text("区间1～5") == "区间1 5"

    def test_corner_brackets(self):
        """Verify CJK corner quotes 「」 and 『』 are removed."""
        assert sanitize_tts_text("他说：「你好」") == "他说： 你好"
        assert sanitize_tts_text("引用『关键结论』说明") == "引用 关键结论 说明"

    def test_existing_suppressed_characters(self):
        """Verify existing brackets, slashes, enumeration comma, pipes are removed."""
        assert sanitize_tts_text("【导语】Hello｜world") == "导语 Hello world"
        assert sanitize_tts_text("速度/力量（power）") == "速度 力量 power"
        assert sanitize_tts_text("苹果、香蕉、橙子") == "苹果 香蕉 橙子"
        assert sanitize_tts_text("Normal (note) text") == "Normal note text"

    def test_empty_and_none(self):
        """Verify empty and None inputs return empty string."""
        assert sanitize_tts_text(None) == ""
        assert sanitize_tts_text("") == ""
        assert sanitize_tts_text("    ") == ""

    def test_multiline_preserved(self):
        """Verify newlines are preserved for sentence splitting."""
        input_text = "《第一章》- 开始\n《第二章》- 结束"
        expected = "第一章 开始\n第二章 结束"
        assert sanitize_tts_text(input_text) == expected
