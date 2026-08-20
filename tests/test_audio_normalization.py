"""
Unit tests for audio normalization utilities (LUFS loudness normalization).

Tests cover:
- LUFS measurement accuracy
- Normalization with various loudness levels
- Torch tensor and numpy array support
- Edge cases (silent audio, clipping audio, extreme loudness)
- Fallback behavior when pyloudnorm is unavailable
- Error handling and warnings
"""

import warnings
from unittest import mock

import numpy as np
import pytest

# Try importing torch for tensor tests
try:
    import torch

    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    torch = None

from indextts.utils.audio_normalization import (
    check_normalization_available,
    get_audio_lufs,
    normalize_loudness,
)


class TestNormalizationAvailability:
    """Test normalization availability detection."""

    def test_check_normalization_available(self):
        """Test that we can check if pyloudnorm is available."""
        result = check_normalization_available()
        assert isinstance(result, bool)

    def test_normalization_availability_matches_import(self):
        """Test that availability check matches actual import."""
        is_available = check_normalization_available()

        try:
            import pyloudnorm

            expected = True
        except ImportError:
            expected = False

        assert is_available == expected


class TestNormalizeLoudness:
    """Test the main normalize_loudness function."""

    @pytest.fixture
    def sample_audio_numpy(self):
        """Create sample audio as numpy array (1 second at 24kHz)."""
        sample_rate = 24000
        duration = 1.0
        frequency = 440.0  # A4 note

        t = np.linspace(0, duration, int(sample_rate * duration))
        audio = np.sin(2 * np.pi * frequency * t).astype(np.float32) * 0.5

        return audio, sample_rate

    @pytest.fixture
    def sample_audio_torch(self, sample_audio_numpy):
        """Create sample audio as torch tensor."""
        if not TORCH_AVAILABLE:
            pytest.skip("PyTorch not available")

        audio, sample_rate = sample_audio_numpy
        return torch.from_numpy(audio), sample_rate

    def test_normalize_numpy_array(self, sample_audio_numpy):
        """Test normalization with numpy array input."""
        audio, sample_rate = sample_audio_numpy

        normalized, metrics = normalize_loudness(
            audio=audio,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        # Check return types
        assert isinstance(normalized, np.ndarray)
        assert isinstance(metrics, dict)

        # Check metrics structure
        assert "original_lufs" in metrics
        assert "target_lufs" in metrics
        assert "gain_db" in metrics
        assert "method" in metrics

        # Check that normalization was applied (if pyloudnorm available)
        if check_normalization_available():
            assert metrics["method"] in ["lufs_bs1770", "skipped_silent"]
            assert metrics["target_lufs"] == -16.0
        else:
            assert metrics["method"] == "peak_fallback"

    @pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch not available")
    def test_normalize_torch_tensor(self, sample_audio_torch):
        """Test normalization with torch tensor input."""
        audio, sample_rate = sample_audio_torch

        normalized, metrics = normalize_loudness(
            audio=audio,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        # Check return type matches input type
        assert torch.is_tensor(normalized)
        assert isinstance(metrics, dict)

        # Check device and dtype preservation
        assert normalized.device == audio.device
        assert normalized.dtype == audio.dtype

    def test_disable_normalization(self, sample_audio_numpy):
        """Test that normalization can be disabled."""
        audio, sample_rate = sample_audio_numpy

        normalized, metrics = normalize_loudness(
            audio=audio,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=False,
            verbose=False,
        )

        # Should return original audio unchanged
        np.testing.assert_array_equal(normalized, audio)
        assert metrics["method"] == "disabled"
        assert metrics["gain_db"] == 0.0

    def test_silent_audio_handling(self):
        """Test that very quiet/silent audio is handled gracefully."""
        # Create nearly silent audio
        sample_rate = 24000
        audio = np.random.randn(sample_rate) * 0.0001  # Very quiet noise

        normalized, metrics = normalize_loudness(
            audio=audio,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        # Should skip normalization for silent audio
        if check_normalization_available():
            assert metrics["method"] in ["skipped_silent", "peak_fallback"]
        else:
            assert metrics["method"] == "peak_fallback"

    def test_int16_range_audio(self, sample_audio_numpy):
        """Test normalization with audio in int16 range (not normalized to [-1, 1])."""
        audio, sample_rate = sample_audio_numpy

        # Scale to int16-like range (simulating actual TTS output)
        audio_int16_like = audio * 16000

        normalized, metrics = normalize_loudness(
            audio=audio_int16_like,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        # Should handle conversion internally
        assert isinstance(normalized, np.ndarray)
        assert metrics["method"] in ["lufs_bs1770", "skipped_silent", "peak_fallback"]

        # CRITICAL: Output should remain in int16 range (not converted to [-1, 1])
        # This ensures audio doesn't get muted when saved with .type(torch.int16)
        assert np.abs(normalized).max() > 10.0, (
            f"Normalized audio should remain in int16 range but got max={np.abs(normalized).max():.4f}. "
            "This would cause audio to be muted when saved!"
        )

        # Verify audio would survive int16 conversion (simulating torchaudio.save)
        if TORCH_AVAILABLE:
            import torch

            normalized_torch = torch.from_numpy(normalized)
            as_int16 = normalized_torch.type(torch.int16)
            # Should have meaningful amplitude after int16 conversion
            assert as_int16.abs().max() > 100, (
                f"Audio is muted after int16 conversion! max={as_int16.abs().max()}"
            )

    def test_multichannel_audio(self):
        """Test normalization with multichannel audio."""
        sample_rate = 24000
        duration = 1.0

        # Create stereo audio (2 channels)
        t = np.linspace(0, duration, int(sample_rate * duration))
        left = np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.5
        right = np.sin(2 * np.pi * 554.37 * t).astype(np.float32) * 0.5  # C#5

        # Shape: (2, samples) or (samples, 2) - both should work
        audio_channels_first = np.stack([left, right])

        normalized, metrics = normalize_loudness(
            audio=audio_channels_first,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        # Should handle multichannel audio
        assert isinstance(normalized, np.ndarray)
        assert normalized.ndim == 2

    def test_invalid_sample_rate(self, sample_audio_numpy):
        """Test error handling for invalid sample rate."""
        audio, _ = sample_audio_numpy

        with pytest.raises(ValueError, match="Invalid sample_rate"):
            normalize_loudness(
                audio=audio,
                sample_rate=0,  # Invalid
                target_lufs=-16.0,
            )

    def test_empty_audio(self):
        """Test error handling for empty audio."""
        audio = np.array([])

        with pytest.raises(ValueError, match="Audio input is empty"):
            normalize_loudness(audio=audio, sample_rate=24000, target_lufs=-16.0)

    def test_target_lufs_values(self, sample_audio_numpy):
        """Test normalization with different target LUFS values."""
        audio, sample_rate = sample_audio_numpy

        target_values = [-14.0, -16.0, -18.0, -20.0, -23.0]

        for target_lufs in target_values:
            normalized, metrics = normalize_loudness(
                audio=audio,
                sample_rate=sample_rate,
                target_lufs=target_lufs,
                enable_normalization=True,
                verbose=False,
            )

            assert isinstance(normalized, np.ndarray)
            if metrics["method"] != "disabled":
                assert metrics["target_lufs"] == target_lufs


class TestGetAudioLufs:
    """Test the get_audio_lufs helper function."""

    def test_measure_lufs_numpy(self):
        """Test LUFS measurement with numpy array."""
        sample_rate = 24000
        duration = 1.0

        # Create test audio
        t = np.linspace(0, duration, int(sample_rate * duration))
        audio = np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.5

        lufs = get_audio_lufs(audio, sample_rate)

        if check_normalization_available():
            # Should return a float LUFS value
            assert isinstance(lufs, float)
            assert -100 < lufs < 0  # Reasonable LUFS range
        else:
            # Should return None if pyloudnorm unavailable
            assert lufs is None

    @pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch not available")
    def test_measure_lufs_torch(self):
        """Test LUFS measurement with torch tensor."""
        sample_rate = 24000
        duration = 1.0

        # Create test audio
        t = np.linspace(0, duration, int(sample_rate * duration))
        audio = torch.from_numpy(np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.5)

        lufs = get_audio_lufs(audio, sample_rate)

        if check_normalization_available():
            assert isinstance(lufs, float)
            assert -100 < lufs < 0
        else:
            assert lufs is None

    def test_measure_silent_audio(self):
        """Test LUFS measurement on silent audio."""
        sample_rate = 24000
        audio = np.zeros(sample_rate)  # 1 second of silence

        lufs = get_audio_lufs(audio, sample_rate)

        if check_normalization_available():
            # Silent audio should return None or very low LUFS
            assert lufs is None or lufs < -70
        else:
            assert lufs is None


class TestFallbackBehavior:
    """Test fallback behavior when pyloudnorm is unavailable."""

    def test_fallback_to_peak_normalization(self, monkeypatch):
        """Test that system falls back to peak normalization when pyloudnorm unavailable."""
        # Mock pyloudnorm as unavailable
        import indextts.utils.audio_normalization as norm_module

        monkeypatch.setattr(norm_module, "PYLOUDNORM_AVAILABLE", False)
        monkeypatch.setattr(norm_module, "pyln", None)

        # Create test audio
        sample_rate = 24000
        audio = np.random.randn(sample_rate).astype(np.float32) * 0.5

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            normalized, metrics = normalize_loudness(
                audio=audio,
                sample_rate=sample_rate,
                target_lufs=-16.0,
                enable_normalization=True,
                verbose=False,
            )

            # Should issue warning about fallback
            assert any(
                "pyloudnorm not available" in str(warning.message) for warning in w
            )

        # Should use peak fallback
        assert metrics["method"] == "peak_fallback"
        assert isinstance(normalized, np.ndarray)


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.fixture
    def sample_audio_numpy(self):
        """Create sample audio as numpy array (1 second at 24kHz)."""
        sample_rate = 24000
        duration = 1.0
        frequency = 440.0  # A4 note

        t = np.linspace(0, duration, int(sample_rate * duration))
        audio = np.sin(2 * np.pi * frequency * t).astype(np.float32) * 0.5

        return audio, sample_rate

    def test_extremely_loud_audio(self):
        """Test normalization on clipped/extremely loud audio."""
        sample_rate = 24000
        # Create clipped audio at max amplitude
        audio = np.ones(sample_rate, dtype=np.float32)

        normalized, metrics = normalize_loudness(
            audio=audio,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        # Should handle without crashing
        assert isinstance(normalized, np.ndarray)
        assert metrics["method"] in ["lufs_bs1770", "peak_fallback", "skipped_silent"]

    def test_very_short_audio(self):
        """Test normalization on very short audio clips."""
        sample_rate = 24000
        # 100ms of audio
        audio = np.random.randn(int(sample_rate * 0.1)).astype(np.float32) * 0.5

        normalized, metrics = normalize_loudness(
            audio=audio,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        # Should handle without crashing
        assert isinstance(normalized, np.ndarray)

    def test_unsupported_audio_shape(self):
        """Test error handling for unsupported audio shapes."""
        sample_rate = 24000
        # 3D array (unsupported)
        audio = np.random.randn(2, 2, sample_rate).astype(np.float32)

        with pytest.raises(ValueError, match="Unsupported audio shape"):
            normalize_loudness(
                audio=audio,
                sample_rate=sample_rate,
                target_lufs=-16.0,
                enable_normalization=True,
                verbose=False,
            )

    def test_verbose_mode(self, sample_audio_numpy):
        """Test that verbose mode produces output without errors."""
        audio, sample_rate = sample_audio_numpy

        # Should not raise any exceptions in verbose mode
        normalized, metrics = normalize_loudness(
            audio=audio,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=True,  # Enable verbose output
        )

        assert isinstance(normalized, np.ndarray)
        assert isinstance(metrics, dict)


class TestConsistency:
    """Test consistency and reproducibility of normalization."""

    @pytest.fixture
    def sample_audio_numpy(self):
        """Create sample audio as numpy array (1 second at 24kHz)."""
        sample_rate = 24000
        duration = 1.0
        frequency = 440.0  # A4 note

        t = np.linspace(0, duration, int(sample_rate * duration))
        audio = np.sin(2 * np.pi * frequency * t).astype(np.float32) * 0.5

        return audio, sample_rate

    def test_normalization_reproducibility(self, sample_audio_numpy):
        """Test that normalization produces consistent results."""
        audio, sample_rate = sample_audio_numpy

        # Run normalization twice
        normalized1, metrics1 = normalize_loudness(
            audio=audio.copy(),
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        normalized2, metrics2 = normalize_loudness(
            audio=audio.copy(),
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        # Results should be identical
        np.testing.assert_array_almost_equal(normalized1, normalized2)
        assert metrics1["method"] == metrics2["method"]

        if metrics1["original_lufs"] is not None:
            assert abs(metrics1["original_lufs"] - metrics2["original_lufs"]) < 0.1

    def test_gain_application_correctness(self, sample_audio_numpy):
        """Test that gain is applied correctly."""
        audio, sample_rate = sample_audio_numpy

        normalized, metrics = normalize_loudness(
            audio=audio,
            sample_rate=sample_rate,
            target_lufs=-16.0,
            enable_normalization=True,
            verbose=False,
        )

        if check_normalization_available() and metrics["method"] == "lufs_bs1770":
            # Measure LUFS of normalized audio
            final_lufs = get_audio_lufs(normalized, sample_rate)

            if final_lufs is not None:
                # Should be very close to target (within 1 dB)
                assert abs(final_lufs - metrics["target_lufs"]) < 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
