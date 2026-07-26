"""
IndexTTS: Zero-shot text-to-speech with platform-specific implementations.

- Windows/Linux: GPU-based inference using IndexTTS models
- macOS: Native TTS using AVFoundation (lightweight alternative)
"""

from indextts.infer import IndexTTS, create_tts_engine

__all__ = ["IndexTTS", "create_tts_engine"]

__version__ = "1.0.0"
