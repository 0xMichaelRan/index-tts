"""Unit tests for bilingual word-count metrics."""

from services.text_metrics import count_words


class TestCountWords:
    def test_english_words(self):
        assert count_words("Hello world") == 2
        assert count_words("There is a vehicle arriving in dock number 7") == 9

    def test_chinese_characters(self):
        assert count_words("你好世界") == 4
        assert count_words("大家好") == 3

    def test_mixed_zh_en(self):
        assert count_words("你好 hello") == 3
        assert count_words("AI技术已经发展到这样匪夷所思的地步了") == 18

    def test_punctuation_ignored(self):
        assert count_words("Hello, world!") == 2
        assert count_words("你好，世界。") == 4

    def test_empty_and_none(self):
        assert count_words("") == 0
        assert count_words("   ") == 0
        assert count_words(None) == 0

    def test_numbers_count_as_tokens(self):
        assert count_words("Order 123 ready") == 3
