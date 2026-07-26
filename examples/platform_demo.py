"""
Platform-specific TTS demo.

This example demonstrates how to use the unified API across platforms:
- Windows/Linux: GPU-based IndexTTS inference
- macOS: Native AVFoundation TTS
"""

import platform
from indextts import create_tts_engine


def main():
    print(f"Running on: {platform.system()}")
    
    # Create the appropriate TTS engine for this platform
    # On macOS: Uses native TTS
    # On Windows/Linux: Uses IndexTTS GPU inference
    tts = create_tts_engine()
    
    text = "Hello, this is a platform-specific text-to-speech demo."
    output_path = "demo_output.wav"
    
    print(f"Synthesizing: {text}")
    
    if platform.system() == "Darwin":
        # macOS native TTS
        print("Note: macOS native TTS will speak to system audio.")
        print("Audio file saving is experimental on macOS.")
        tts.infer_to_system_audio(text, rate=0.5)
        print("Speech completed!")
    else:
        # Windows/Linux GPU inference
        audio_prompt = "test_data/input.wav"  # Reference audio
        result = tts.infer(
            audio_prompt=audio_prompt,
            text=text,
            output_path=output_path,
            verbose=False
        )
        print(f"Audio saved to: {result}")


def macos_advanced_demo():
    """Advanced macOS TTS demo with voice selection."""
    if platform.system() != "Darwin":
        print("This demo is macOS-only")
        return
    
    from indextts.macos_tts import MacOSTTS
    
    # Create macOS TTS engine
    tts = MacOSTTS(language="en-US")
    
    # List available voices
    print("Available English voices:")
    voices = tts.list_voices(language="en")
    for v in voices[:5]:  # Show first 5
        print(f"  - {v['name']} ({v['identifier']})")
    
    # Synthesize with different parameters
    texts = [
        "This is normal speech rate.",
        "This is faster speech.",
        "This has higher pitch.",
    ]
    
    rates = [0.5, 0.7, 0.5]
    pitches = [1.0, 1.0, 1.5]
    
    for text, rate, pitch in zip(texts, rates, pitches):
        print(f"\nSpeaking: {text}")
        print(f"  Rate: {rate}, Pitch: {pitch}")
        tts.infer_to_system_audio(text, rate=rate, pitch=pitch)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Platform TTS Demo")
    parser.add_argument(
        "--macos-advanced",
        action="store_true",
        help="Run advanced macOS demo (macOS only)"
    )
    
    args = parser.parse_args()
    
    if args.macos_advanced:
        macos_advanced_demo()
    else:
        main()
