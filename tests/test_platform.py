#!/usr/bin/env python3
"""
Quick test script to verify platform-specific TTS implementation.

Usage:
    pytest test_platform.py -v -s
"""

import platform
import sys
import pytest


class TestImports:
    """Test that all imports work correctly."""

    def test_core_imports(self):
        """Test core module imports."""
        from indextts import create_tts_engine, IndexTTS

        assert create_tts_engine is not None
        assert IndexTTS is not None
        print("✓ Core imports successful")


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-only test")
class TestMacOSTTSPlatform:
    """Test macOS native TTS platform integration."""

    @pytest.fixture(scope="class")
    def macos_tts(self):
        """Create MacOSTTS instance if available."""
        pytest.importorskip(
            "AVFoundation",
            reason="macOS TTS dependencies not installed. Install with: pip install 'indextts-worker[mac]'",
        )

        from indextts.macos_tts import MacOSTTS

        tts = MacOSTTS(language="en-US")
        yield tts
        del tts

    def test_macos_tts_creation(self, macos_tts):
        """Test MacOSTTS engine creation."""
        assert macos_tts is not None
        print("✓ Created MacOSTTS engine")

    def test_list_voices_macos(self, macos_tts):
        """Test listing available voices."""
        voices = macos_tts.list_voices(language="en")

        assert len(voices) > 0, "No English voices found"
        print(f"✓ Found {len(voices)} English voices")

        if voices:
            print(f"  Example: {voices[0]['name']}")

    def test_synthesis_to_audio_macos(self, macos_tts):
        """Test speech synthesis to system audio."""
        test_text = "This is a test of the macOS native text to speech system."
        print(f"\n  Speaking: '{test_text}'")
        print("  (You should hear audio from your Mac speakers)")

        macos_tts.infer_to_system_audio(test_text, rate=0.5)
        print("✓ Speech synthesis completed")


class TestFactory:
    """Test the TTS factory function."""

    def test_factory_function_exists(self):
        """Test that create_tts_engine function exists."""
        from indextts import create_tts_engine

        assert callable(create_tts_engine)

    def test_factory_creates_engine(self):
        """Test factory function creates appropriate engine."""
        from indextts import create_tts_engine

        try:
            tts = create_tts_engine()
            assert tts is not None
            print(f"✓ Factory created TTS engine: {type(tts).__name__}")

        except RuntimeError as e:
            # Expected if dependencies aren't installed
            if "PyTorch" in str(e):
                pytest.skip(
                    "PyTorch not available (expected on macOS without GPU). "
                    "Install with: pip install 'indextts-worker[cuda]'"
                )
            elif "macOS native TTS" in str(e):
                pytest.skip(
                    "macOS TTS dependencies not installed. "
                    "Install with: pip install 'indextts-worker[mac]'"
                )
            else:
                raise


@pytest.mark.skipif(platform.system() == "Darwin", reason="CUDA not available on macOS")
class TestGPUInference:
    """Test GPU inference availability (non-macOS only)."""

    def test_cuda_availability(self):
        """Test CUDA availability and IndexTTS imports."""
        torch = pytest.importorskip(
            "torch",
            reason="PyTorch not installed. Install with: pip install 'indextts-worker[cuda]'",
        )

        if torch.cuda.is_available():
            print(f"✓ CUDA available: {torch.cuda.get_device_name(0)}")

            from indextts import IndexTTS

            assert IndexTTS is not None
            print("✓ IndexTTS imports successful")
            print("  (Skipping model loading for quick test)")
        else:
            print("  CUDA not available (CPU-only mode)")
            pytest.skip("CUDA not available on this system")


# Utility test for platform information
def test_platform_info():
    """Display platform information."""
    print(f"\n{'=' * 60}")
    print(f"Platform: {platform.system()} {platform.release()}")
    print(f"Python: {sys.version}")
    print(f"{'=' * 60}")


# For backwards compatibility with direct script execution
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
