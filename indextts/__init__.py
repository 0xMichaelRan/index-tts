"""
IndexTTS: Zero-shot text-to-speech with platform-specific implementations.

- Windows/Linux: GPU-based inference using IndexTTS models
- macOS: Native TTS using AVFoundation (lightweight alternative)
"""

try:
    from indextts.infer import IndexTTS, create_tts_engine
    __all__ = ["IndexTTS", "create_tts_engine"]
except ImportError as e:
    # Allow partial imports if some dependencies are missing
    # (e.g., macOS without PyTorch installed)
    __all__ = []
    import warnings
    warnings.warn(
        f"Could not fully import IndexTTS. Some features may be unavailable. Error: {e}",
        ImportWarning
    )

__version__ = "1.0.0"
