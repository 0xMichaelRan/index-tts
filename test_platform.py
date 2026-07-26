#!/usr/bin/env python3
"""
Quick test script to verify platform-specific TTS implementation.

Usage:
    python test_platform.py
"""

import platform
import sys


def test_imports():
    """Test that all imports work correctly."""
    print("Testing imports...")

    try:
        from indextts import create_tts_engine, IndexTTS

        print("✓ Core imports successful")
        return True
    except ImportError as e:
        print(f"✗ Failed to import core modules: {e}")
        return False


def test_macos_tts():
    """Test macOS native TTS."""
    if platform.system() != "Darwin":
        print("\nSkipping macOS TTS test (not on macOS)")
        return True

    print("\nTesting macOS native TTS...")

    try:
        from indextts.macos_tts import MacOSTTS

        # Create TTS engine
        tts = MacOSTTS(language="en-US")
        print(f"✓ Created MacOSTTS engine")

        # List voices
        voices = tts.list_voices(language="en")
        print(f"✓ Found {len(voices)} English voices")
        if voices:
            print(f"  Example: {voices[0]['name']}")

        # Test synthesis (to system audio)
        test_text = "This is a test of the macOS native text to speech system."
        print(f"\n  Speaking: '{test_text}'")
        print("  (You should hear audio from your Mac speakers)")

        tts.infer_to_system_audio(test_text, rate=0.5)
        print("✓ Speech synthesis completed")

        return True

    except ImportError as e:
        print(f"ℹ macOS TTS not installed: {e}")
        print("  Install with: pip install 'indextts-worker[mac]'")
        return True  # Not a failure, just not installed

    except Exception as e:
        print(f"✗ macOS TTS test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_factory():
    """Test the factory function."""
    print("\nTesting factory function...")

    try:
        from indextts import create_tts_engine
        import platform

        # Try to create engine (may fail if dependencies missing)
        try:
            tts = create_tts_engine()
            print(f"✓ Factory created TTS engine: {type(tts).__name__}")
            return True
        except RuntimeError as e:
            # Expected if dependencies aren't installed
            if "PyTorch" in str(e):
                print(f"ℹ PyTorch not available (expected on macOS without GPU)")
                print(f"  Install with: pip install 'indextts-worker[cuda]'")
                return True
            elif "macOS native TTS" in str(e):
                print(f"ℹ macOS TTS dependencies not installed")
                print(f"  Install with: pip install 'indextts-worker[mac]'")
                return True
            else:
                raise

    except Exception as e:
        print(f"✗ Factory test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_gpu_inference():
    """Test GPU inference (if available)."""
    if platform.system() == "Darwin":
        print("\nSkipping GPU inference test (macOS, no CUDA)")
        return True

    print("\nTesting GPU inference availability...")

    try:
        import torch

        if torch.cuda.is_available():
            print(f"✓ CUDA available: {torch.cuda.get_device_name(0)}")

            from indextts import IndexTTS

            print("✓ IndexTTS imports successful")

            # Don't actually load models (too slow for quick test)
            print("  (Skipping model loading for quick test)")

        else:
            print("  CUDA not available (CPU-only mode)")

        return True

    except ImportError as e:
        print(f"  PyTorch not installed: {e}")
        print("  Install with: pip install 'indextts-worker[cuda]'")
        return True  # Not a failure, just not installed


def main():
    """Run all tests."""
    print("=" * 60)
    print(f"Platform: {platform.system()} {platform.release()}")
    print(f"Python: {sys.version}")
    print("=" * 60)

    results = []

    results.append(("Imports", test_imports()))
    results.append(("Factory", test_factory()))

    if platform.system() == "Darwin":
        results.append(("macOS TTS", test_macos_tts()))
    else:
        results.append(("GPU Inference", test_gpu_inference()))

    print("\n" + "=" * 60)
    print("Test Results:")
    print("=" * 60)

    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} {name}")

    all_passed = all(passed for _, passed in results)

    print("=" * 60)
    if all_passed:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
