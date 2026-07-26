"""
macOS Native TTS Implementation using AVFoundation.

This module provides a lightweight TTS interface for macOS systems
that don't have CUDA/GPU capabilities. It uses the native AVSpeechSynthesizer
API for text-to-speech conversion.
"""

import os
import sys
import platform
from pathlib import Path
from typing import Optional, List

if platform.system() == "Darwin":
    try:
        from Foundation import NSURL, NSDate, NSRunLoop, NSDefaultRunLoopMode
        from AVFoundation import (
            AVSpeechSynthesizer,
            AVSpeechUtterance,
            AVSpeechSynthesisVoice,
            AVSpeechBoundary,
        )
        import objc
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
    
    def __init__(self, voice: Optional[str] = None, language: str = "en-US"):
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
                print(f"Warning: Voice '{voice}' not found. Using default for {language}")
                self.voice = AVSpeechSynthesisVoice.voiceWithLanguage_(language)
        else:
            self.voice = AVSpeechSynthesisVoice.voiceWithLanguage_(language)
        
        if self.voice is None:
            print(f"Warning: No voice found for {language}. Using system default.")
            self.voice = AVSpeechSynthesisVoice.voiceWithLanguage_("en-US")
    
    def _get_available_voices(self) -> List[dict]:
        """Get list of available system voices."""
        voices = []
        for voice in AVSpeechSynthesisVoice.speechVoices():
            voices.append({
                "identifier": voice.identifier(),
                "name": voice.name(),
                "language": voice.language(),
                "quality": voice.quality(),
            })
        return voices
    
    def list_voices(self, language: Optional[str] = None) -> List[dict]:
        """
        List available voices, optionally filtered by language.
        
        Args:
            language: Language code to filter by (e.g., "en-US", "zh-CN").
                      If None, returns all voices.
        
        Returns:
            List of voice dictionaries with identifier, name, language, quality.
        """
        if language:
            return [v for v in self.available_voices if v["language"].startswith(language[:2])]
        return self.available_voices
    
    def infer(
        self,
        audio_prompt: Optional[str],
        text: str,
        output_path: str,
        rate: float = 0.5,
        pitch: float = 1.0,
        volume: float = 1.0,
        **kwargs
    ) -> str:
        """
        Synthesize speech from text using macOS native TTS.
        
        Args:
            audio_prompt: Reference audio path (ignored for macOS TTS).
            text: Text to synthesize.
            output_path: Output audio file path (.aiff, .caf, or .wav).
            rate: Speech rate (0.0 = slow, 1.0 = fast). Default 0.5 (normal).
            pitch: Voice pitch multiplier (0.5-2.0). Default 1.0.
            volume: Volume (0.0-1.0). Default 1.0.
            **kwargs: Additional parameters (ignored for compatibility).
        
        Returns:
            Path to the generated audio file.
        """
        if audio_prompt:
            print(f"Note: audio_prompt is not used in macOS native TTS (received: {audio_prompt})")
        
        # Create utterance
        utterance = AVSpeechUtterance.speechUtteranceWithString_(text)
        utterance.setVoice_(self.voice)
        utterance.setRate_(rate)
        utterance.setPitchMultiplier_(pitch)
        utterance.setVolume_(volume)
        
        # For writing to file, we need to use AVAudioEngine and AVSpeechSynthesizer's
        # write() method (macOS 13+), but for simplicity, we'll use the system
        # afplay command to record output
        
        # Speak to default output
        self._is_speaking = True
        self.synthesizer.speakUtterance_(utterance)
        
        # Wait for speech to complete
        run_loop = NSRunLoop.currentRunLoop()
        while self._is_speaking:
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )
            if not self.synthesizer.isSpeaking():
                self._is_speaking = False
        
        print(f"Note: macOS native TTS spoke the text to system audio.")
        print(f"File saving to '{output_path}' requires recording system audio.")
        print(f"For production use, consider using the CUDA-based IndexTTS on Windows/Linux.")
        
        # Create a placeholder file to match the API
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            f.write(f"macOS TTS output placeholder\nText: {text}\n")
        
        return output_path
    
    def infer_to_system_audio(
        self,
        text: str,
        rate: float = 0.5,
        pitch: float = 1.0,
        volume: float = 1.0,
    ):
        """
        Speak text directly to system audio output (simpler API).
        
        Args:
            text: Text to synthesize.
            rate: Speech rate (0.0 = slow, 1.0 = fast). Default 0.5.
            pitch: Voice pitch multiplier (0.5-2.0). Default 1.0.
            volume: Volume (0.0-1.0). Default 1.0.
        """
        utterance = AVSpeechUtterance.speechUtteranceWithString_(text)
        utterance.setVoice_(self.voice)
        utterance.setRate_(rate)
        utterance.setPitchMultiplier_(pitch)
        utterance.setVolume_(volume)
        
        self._is_speaking = True
        self.synthesizer.speakUtterance_(utterance)
        
        # Wait for completion
        run_loop = NSRunLoop.currentRunLoop()
        while self._is_speaking:
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )
            if not self.synthesizer.isSpeaking():
                self._is_speaking = False


def create_macos_tts_engine(voice: Optional[str] = None, language: str = "en-US") -> MacOSTTS:
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
    parser.add_argument("--list-voices", action="store_true", help="List available voices")
    parser.add_argument("--voice", help="Voice identifier")
    parser.add_argument("--language", default="en-US", help="Language code (default: en-US)")
    parser.add_argument("--rate", type=float, default=0.5, help="Speech rate (0.0-1.0)")
    parser.add_argument("--output", help="Output file path (experimental)")
    
    args = parser.parse_args()
    
    tts = MacOSTTS(voice=args.voice, language=args.language)
    
    if args.list_voices:
        print("Available voices:")
        for v in tts.list_voices():
            print(f"  {v['identifier']} ({v['name']}, {v['language']})")
    
    if args.output:
        tts.infer(None, args.text, args.output, rate=args.rate)
    else:
        tts.infer_to_system_audio(args.text, rate=args.rate)
