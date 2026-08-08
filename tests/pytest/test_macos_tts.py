#!/usr/bin/env python3
"""
Simple test script for macOS native TTS functionality.
"""

import platform
import pytest


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-only test")
class TestMacOSTTS:
    """Test suite for macOS native TTS functionality."""

    @pytest.fixture(scope="class")
    def tts_engine(self):
        """Create and return a macOS TTS engine instance."""
        try:
            from indextts.infer import create_tts_engine

            engine = create_tts_engine(use_native_macos=True, language="en-US")
            yield engine
            # Cleanup if needed
            del engine
        except RuntimeError as e:
            if "macOS native TTS" in str(e):
                pytest.skip(
                    "macOS TTS dependencies not installed. "
                    "Install with: pip install 'indextts-worker[mac]'"
                )
            raise

    def test_engine_creation(self, tts_engine):
        """Test that macOS TTS engine can be created successfully."""
        assert tts_engine is not None
        print("✓ Engine created successfully")

    def test_list_voices(self, tts_engine):
        """Test listing available English voices."""
        voices = tts_engine.list_voices(language="en")

        assert len(voices) > 0, "No English voices found"
        assert all("name" in v and "identifier" in v for v in voices), (
            "Voice entries missing required fields"
        )

        print(f"\nAvailable voices (English): {len(voices)} voices")
        for i, voice in enumerate(voices[:5], 1):
            print(f"   {i}. {voice['name']} ({voice['identifier']})")
        if len(voices) > 5:
            print(f"   ... and {len(voices) - 5} more")

    def test_speech_synthesis_to_audio(self, tts_engine):
        """Test speech synthesis to system audio."""
        test_text = (
            "Hello! This is the IndexTTS worker running on macOS using AVFoundation."
        )

        # Should not raise any exceptions
        tts_engine.infer_to_system_audio(test_text, ratio=1.0, pitch=1.0, volume=1.0)
        print("✓ Speech synthesis to system audio completed")

    def test_file_output(self, tts_engine, tmp_path):
        """Test speech synthesis to file output."""
        output_path = tmp_path / "macos_test.wav"

        tts_engine.infer(
            audio_prompt=None,
            text="This is a test of file output.",
            output_path=str(output_path),
            ratio=1.0,
        )

        assert output_path.exists(), f"Output file not created at {output_path}"
        print(f"✓ Output written to: {output_path}")


def test_platform_check():
    """Test that reports platform information."""
    print(f"\nPlatform: {platform.system()}")
    if platform.system() != "Darwin":
        pytest.skip("Skipping macOS TTS tests on non-macOS platform")


# For backwards compatibility with direct script execution
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
