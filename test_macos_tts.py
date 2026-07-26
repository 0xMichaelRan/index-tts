#!/usr/bin/env python3
"""
Simple test script for macOS native TTS functionality.
"""

import platform
from indextts.infer import create_tts_engine


def main():
    print(f"Platform: {platform.system()}")

    if platform.system() != "Darwin":
        print("❌ This test is only for macOS systems")
        return

    print("\n=== Testing macOS Native TTS ===\n")

    # Create TTS engine
    print("1. Creating macOS TTS engine...")
    tts = create_tts_engine(use_native_macos=True, language="en-US")
    print("   ✓ Engine created successfully\n")

    # List available voices
    print("2. Available voices (English):")
    voices = tts.list_voices(language="en")
    for i, voice in enumerate(voices[:5], 1):  # Show first 5
        print(f"   {i}. {voice['name']} ({voice['identifier']})")
    if len(voices) > 5:
        print(f"   ... and {len(voices) - 5} more\n")
    else:
        print()

    # Test speech synthesis to system audio
    print("3. Testing speech synthesis (to system audio)...")
    test_text = (
        "Hello! This is the IndexTTS worker running on macOS using AVFoundation."
    )
    tts.infer_to_system_audio(test_text, rate=0.5, pitch=1.0, volume=1.0)
    print("   ✓ Speech synthesis completed\n")

    # Test file output (creates placeholder)
    print("4. Testing file output (creates placeholder file)...")
    output_path = "outputs/test/macos_test.wav"
    tts.infer(
        audio_prompt=None,
        text="This is a test of file output.",
        output_path=output_path,
        rate=0.5,
    )
    print(f"   ✓ Output written to: {output_path}\n")

    print("=== All tests passed! ===\n")
    print("Note: macOS TTS uses AVFoundation's AVSpeechSynthesizer.")
    print("This is a lightweight alternative to GPU-based IndexTTS.")
    print("For production voice cloning, use IndexTTS with CUDA on Windows/Linux.\n")


if __name__ == "__main__":
    main()
