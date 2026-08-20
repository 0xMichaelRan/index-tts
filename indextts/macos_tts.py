"""
macOS Native TTS Implementation using AVFoundation.

This module provides a lightweight TTS interface for macOS systems
that don't have CUDA/GPU capabilities. It uses the native AVSpeechSynthesizer
API for text-to-speech conversion.
"""

import os
import platform

if platform.system() == "Darwin":
    try:
        from AVFoundation import (
            AVSpeechSynthesisVoice,
            AVSpeechSynthesizer,
            AVSpeechUtterance,
        )
        from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop
    except ImportError:
        raise ImportError(
            "macOS TTS requires pyobjc-framework-AVFoundation. "
            "Install with: pip install 'indextts-worker[mac]'"
        )
else:
    # Stub imports for type hints on non-macOS systems
    AVSpeechSynthesizer = None
    AVSpeechUtterance = None
    AVSpeechSynthesisVoice = None


class MacOSTTS:
    """
    macOS Native TTS using AVFoundation's AVSpeechSynthesizer.

    This is a lightweight alternative to GPU-based IndexTTS for development
    and testing on macOS systems without CUDA support.
    """

    def __init__(self, voice: str | None = None, language: str = "en-US"):
        """
        Initialize macOS TTS synthesizer.

        Args:
            voice: Voice identifier (e.g., "com.apple.ttsbundle.Samantha-compact").
                   If None, uses the default system voice for the language.
            language: Language code (e.g., "en-US", "zh-CN", "ja-JP").
        """
        if platform.system() != "Darwin":
            raise RuntimeError("MacOSTTS is only available on macOS systems")

        self.synthesizer = AVSpeechSynthesizer.alloc().init()
        self.language = language
        self.voice_identifier = voice
        self._is_speaking = False
        self._output_path = None

        # Get available voices
        self.available_voices = self._get_available_voices()

        # Set voice
        if voice:
            self.voice = AVSpeechSynthesisVoice.voiceWithIdentifier_(voice)
            if self.voice is None:
                print(
                    f"Warning: Voice '{voice}' not found. Using default for {language}"
                )
                self.voice = AVSpeechSynthesisVoice.voiceWithLanguage_(language)
        else:
            self.voice = AVSpeechSynthesisVoice.voiceWithLanguage_(language)

        if self.voice is None:
            print(f"Warning: No voice found for {language}. Using system default.")
            self.voice = AVSpeechSynthesisVoice.voiceWithLanguage_("en-US")

    def _get_available_voices(self) -> list[dict]:
        """Get list of available system voices."""
        voices = []
        for voice in AVSpeechSynthesisVoice.speechVoices():
            voices.append(
                {
                    "identifier": voice.identifier(),
                    "name": voice.name(),
                    "language": voice.language(),
                    "quality": voice.quality(),
                }
            )
        return voices

    def list_voices(self, language: str | None = None) -> list[dict]:
        """
        List available voices, optionally filtered by language.

        Args:
            language: Language code to filter by (e.g., "en-US", "zh-CN").
                      If None, returns all voices.

        Returns:
            List of voice dictionaries with identifier, name, language, quality.
        """
        if language:
            return [
                v
                for v in self.available_voices
                if v["language"].startswith(language[:2])
            ]
        return self.available_voices

    def infer(
        self,
        audio_prompt: str | None,
        text: str,
        output_path: str,
        ratio: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        **kwargs,
    ) -> str:
        """
        Synthesize speech from text using macOS native TTS.

        Uses the 'say' command to generate audio files directly without playing
        through system audio.

        Args:
            audio_prompt: Reference audio path (ignored for macOS TTS).
            text: Text to synthesize.
            output_path: Output audio file path (will be saved as .aiff, then converted to .wav).
            ratio: Speech ratio (0.5 = slow, 1.0 = normal, 2.0 = fast). Default 1.0.
            pitch: Voice pitch multiplier (0.5-2.0). Default 1.0 (NOTE: say command doesn't support pitch).
            volume: Volume (0.0-1.0). Default 1.0 (NOTE: say command doesn't support volume).
            **kwargs: Additional parameters (ignored for compatibility).

        Returns:
            Path to the generated audio file (.wav format).
        """
        import subprocess

        if audio_prompt:
            print(
                f"Note: audio_prompt is not used in macOS native TTS (received: {audio_prompt})"
            )

        if pitch != 1.0:
            print(
                f"Warning: pitch parameter ({pitch}) not supported by macOS 'say' command"
            )

        if volume != 1.0:
            print(
                f"Warning: volume parameter ({volume}) not supported by macOS 'say' command"
            )

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # Force .wav extension for consistency with IndexTTS
        if not output_path.endswith(".wav"):
            output_path = os.path.splitext(output_path)[0] + ".wav"

        # Convert ratio (0.5-2.0) to words per minute (100-300 wpm)
        # say command expects wpm, typical range is 100-300
        # ratio=0.5 -> 100wpm (slow), ratio=1.0 -> 200wpm (normal), ratio=2.0 -> 300wpm (fast)
        wpm = int(100 + (ratio * 100))

        # Get voice name (say command uses voice name, not identifier)
        voice_name = self.voice.name() if self.voice else "Samantha"

        # Create temporary AIFF file (say's native format)
        temp_aiff = output_path.replace(".wav", "_temp.aiff")

        try:
            # Build say command to generate AIFF
            cmd_say = ["say", "-v", voice_name, "-r", str(wpm), "-o", temp_aiff, text]

            print(f"Generating audio: voice={voice_name}, words_per_minute={wpm}")
            subprocess.run(cmd_say, check=True, capture_output=True, text=True)

            # Convert AIFF to WAV using afconvert (built into macOS)
            cmd_convert = [
                "afconvert",
                "-f",
                "WAVE",  # Output format: WAVE
                "-d",
                "LEI16",  # Data format: 16-bit little-endian integer PCM
                temp_aiff,
                output_path,
            ]

            print("Converting AIFF to WAV...")
            subprocess.run(cmd_convert, check=True, capture_output=True, text=True)

            print(f"Saved audio to: {output_path}")

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            raise RuntimeError(
                f"Failed to generate audio with macOS tools: {error_msg}"
            )

        finally:
            # Clean up temporary AIFF file
            if os.path.exists(temp_aiff):
                try:
                    os.remove(temp_aiff)
                except OSError as e:
                    print(f"Warning: Failed to remove temporary file {temp_aiff}: {e}")

        return output_path

    def infer_to_system_audio(
        self,
        text: str,
        ratio: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
    ):
        """
        Speak text directly to system audio output (simpler API).

        Args:
            text: Text to synthesize.
            ratio: Speech ratio (0.5 = slow, 1.0 = normal, 2.0 = fast). Default 1.0.
            pitch: Voice pitch multiplier (0.5-2.0). Default 1.0.
            volume: Volume (0.0-1.0). Default 1.0.
        """
        utterance = AVSpeechUtterance.speechUtteranceWithString_(text)
        utterance.setVoice_(self.voice)
        # AVSpeechUtterance uses rate in range 0.0-1.0 where 0.5 is normal
        # Convert our ratio (0.5-2.0, 1.0=normal) to AVSpeech rate (0.0-1.0, 0.5=normal)
        av_rate = ratio * 0.5
        utterance.setRate_(av_rate)
        utterance.setPitchMultiplier_(pitch)
        utterance.setVolume_(volume)

        self._is_speaking = True
        self.synthesizer.speakUtterance_(utterance)

        # Wait for completion
        run_loop = NSRunLoop.currentRunLoop()
        while self._is_speaking:
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )
            if not self.synthesizer.isSpeaking():
                self._is_speaking = False


def create_macos_tts_engine(
    voice: str | None = None, language: str = "en-US"
) -> MacOSTTS:
    """
    Factory function to create a macOS TTS engine.

    Args:
        voice: Voice identifier or None for default.
        language: Language code (default: "en-US").

    Returns:
        MacOSTTS instance.
    """
    return MacOSTTS(voice=voice, language=language)


# CLI interface for testing
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="macOS Native TTS Test")
    parser.add_argument("text", help="Text to synthesize")
    parser.add_argument(
        "--list-voices", action="store_true", help="List available voices"
    )
    parser.add_argument("--voice", help="Voice identifier")
    parser.add_argument(
        "--language", default="en-US", help="Language code (default: en-US)"
    )
    parser.add_argument(
        "--ratio", type=float, default=1.0, help="Speech ratio (0.5-2.0, 1.0=normal)"
    )
    parser.add_argument("--output", help="Output file path (experimental)")

    args = parser.parse_args()

    tts = MacOSTTS(voice=args.voice, language=args.language)

    if args.list_voices:
        print("Available voices:")
        for v in tts.list_voices():
            print(f"  {v['identifier']} ({v['name']}, {v['language']})")

    if args.output:
        tts.infer(None, args.text, args.output, ratio=args.ratio)
    else:
        tts.infer_to_system_audio(args.text, ratio=args.ratio)
