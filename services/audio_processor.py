"""
Audio processing operations: duration detection, time-stretching, normalization.
"""

import os
import shutil
import wave
from datetime import datetime

from services.logging_config import get_logger

logger = get_logger(__name__)


class AudioProcessor:
    """Handles audio processing operations."""

    def __init__(self):
        """Initialize audio processor."""
        pass

    @staticmethod
    def get_audio_duration(audio_path: str) -> float:
        """
        Get duration of audio file in seconds.

        Args:
            audio_path: Path to audio file (WAV format)

        Returns:
            Duration in seconds

        Raises:
            FileNotFoundError: If audio file doesn't exist
            ValueError: If file is not valid WAV
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            with wave.open(audio_path, "r") as audio_file:
                frames = audio_file.getnframes()
                sample_rate = audio_file.getframerate()
                duration = frames / float(sample_rate)
                return duration
        except wave.Error as e:
            raise ValueError(f"Invalid WAV file {audio_path}: {e}")
        except Exception as e:
            logger.warning(f"Could not read audio duration from {audio_path}: {e}")
            # Fallback: estimate from file size (rough approximation)
            # WAV at 24kHz, 16-bit mono: ~48000 bytes/sec
            file_size = os.path.getsize(audio_path)
            estimated_duration = file_size / 48000.0
            logger.info(
                f"Using estimated duration: {estimated_duration:.2f}s based on file size"
            )
            return estimated_duration

    @staticmethod
    def apply_time_stretch(audio_path: str, ratio: float, job_id: str) -> None:
        """
        Apply time-stretching to audio file in-place.

        Uses librosa's time_stretch to adjust playback speed while preserving pitch.
        - ratio > 1.0: Speed up (e.g., 2.0 = 2x faster)
        - ratio = 1.0: No change (normal speed)
        - ratio < 1.0: Slow down (e.g., 0.5 = 2x slower)

        Args:
            audio_path: Path to audio file (WAV format)
            ratio: Time stretch ratio
            job_id: Job identifier for logging

        Raises:
            Exception: If time-stretching fails
        """
        import librosa
        import soundfile as sf

        try:
            # Load audio
            audio, sr = librosa.load(audio_path, sr=None)
            original_duration = len(audio) / sr

            # Apply time stretching
            stretched = librosa.effects.time_stretch(audio, rate=ratio)
            new_duration = len(stretched) / sr

            # Save back to same file
            sf.write(audio_path, stretched, sr)

            logger.info(
                f"[JOB {job_id}] Time stretch applied successfully "
                f"(original: {original_duration:.2f}s → new: {new_duration:.2f}s)"
            )

        except Exception as e:
            logger.error(f"[JOB {job_id}] Time stretching failed: {e}")
            logger.warning(
                f"[JOB {job_id}] Continuing with original audio (no time stretch)"
            )

    @staticmethod
    def copy_audio_file(src_path: str, job_id: str, output_dir: str) -> str:
        """
        Copy audio file to output directory.

        Args:
            src_path: Source audio path
            job_id: Job identifier
            output_dir: Output directory path

        Returns:
            Path to copied file
        """
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        dst_path = os.path.join(output_dir, f"{job_id}_{timestamp}.wav")

        shutil.copy(src_path, dst_path)
        logger.info(f"[JOB {job_id}] Copied audio file to {dst_path}")

        return dst_path

    @staticmethod
    def apply_ratio_to_audio(
        base_audio_path: str, ratio: float, job_id: str, output_dir: str
    ) -> str:
        """
        Apply time-stretching to cached base audio.

        Creates a copy of base audio and applies time-stretching.

        Args:
            base_audio_path: Path to cached base audio
            ratio: Time stretch ratio
            job_id: Job identifier
            output_dir: Output directory

        Returns:
            Path to time-stretched audio file
        """
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        output_path = os.path.join(output_dir, f"{job_id}_{timestamp}.wav")

        # Copy base file
        shutil.copy(base_audio_path, output_path)

        # Apply time-stretching in-place
        logger.info(
            f"[JOB {job_id}] Applying time stretch to cached audio (ratio={ratio})"
        )
        AudioProcessor.apply_time_stretch(output_path, ratio, job_id)

        return output_path
