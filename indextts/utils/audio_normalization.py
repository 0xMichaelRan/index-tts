"""
Audio Normalization Utilities

This module provides LUFS (Loudness Units relative to Full Scale) normalization
for audio using the ITU-R BS.1770-4 standard via the pyloudnorm library.

LUFS is the industry-standard measurement for perceived loudness, used by:
- Streaming platforms (Spotify, YouTube, Apple Music): -14 LUFS
- Broadcasting (EBU R128): -23 LUFS
- TTS/Voice content: -16 to -20 LUFS (chosen: -16 LUFS for clarity)

Key Features:
- Accurate loudness measurement using BS.1770-4 standard
- True peak limiting to prevent clipping after normalization
- Graceful fallback to simple peak normalization if pyloudnorm unavailable
- Support for both torch tensors and numpy arrays
- Comprehensive error handling and logging
"""

import warnings
from typing import Optional, Tuple, Union

import numpy as np

try:
    import pyloudnorm as pyln
    PYLOUDNORM_AVAILABLE = True
except ImportError:
    PYLOUDNORM_AVAILABLE = False
    pyln = None


def normalize_loudness(
    audio: Union[np.ndarray, "torch.Tensor"],
    sample_rate: int,
    target_lufs: float = -16.0,
    true_peak_limit: float = -1.0,
    enable_normalization: bool = True,
    verbose: bool = False
) -> Tuple[Union[np.ndarray, "torch.Tensor"], dict]:
    """
    Normalize audio to target LUFS level using ITU-R BS.1770-4 standard.
    
    Args:
        audio: Input audio as numpy array or torch tensor.
               Shape: (samples,) for mono or (channels, samples) for multi-channel
               Expected dtype: float32 or int16
        sample_rate: Audio sample rate in Hz (e.g., 24000, 22050, 44100)
        target_lufs: Target integrated loudness in LUFS (default: -16.0)
                     Common values:
                     - -14.0: Streaming platforms (Spotify, YouTube)
                     - -16.0: TTS/Voice (good balance, chosen default)
                     - -18.0 to -20.0: Quiet content (podcasts, audiobooks)
                     - -23.0: Broadcasting (EBU R128)
        true_peak_limit: Maximum true peak level in dBFS (default: -1.0)
                        Prevents clipping after normalization
        enable_normalization: If False, returns original audio (for testing/comparison)
        verbose: If True, prints detailed loudness measurements
    
    Returns:
        Tuple of (normalized_audio, metrics_dict):
        - normalized_audio: Same type/shape as input
        - metrics_dict: Contains 'original_lufs', 'target_lufs', 'gain_db', 'method'
    
    Raises:
        ValueError: If audio is empty, sample_rate invalid, or audio format unsupported
    
    Examples:
        >>> # Normalize torch tensor audio
        >>> normalized, metrics = normalize_loudness(wav_tensor, 24000, target_lufs=-16.0)
        >>> print(f"Applied {metrics['gain_db']:.2f} dB gain")
        
        >>> # Disable normalization for comparison
        >>> original, _ = normalize_loudness(wav, 24000, enable_normalization=False)
    """
    # Validation
    if audio is None or (hasattr(audio, 'numel') and audio.numel() == 0) or \
       (isinstance(audio, np.ndarray) and audio.size == 0):
        raise ValueError("Audio input is empty")
    
    if sample_rate <= 0:
        raise ValueError(f"Invalid sample_rate: {sample_rate}. Must be positive.")
    
    if not enable_normalization:
        if verbose:
            print(">> Loudness normalization is disabled, returning original audio")
        return audio, {
            'original_lufs': None,
            'target_lufs': target_lufs,
            'gain_db': 0.0,
            'method': 'disabled'
        }
    
    # Check if pyloudnorm is available
    if not PYLOUDNORM_AVAILABLE:
        warnings.warn(
            "pyloudnorm not available. Falling back to simple peak normalization. "
            "Install with: pip install pyloudnorm",
            category=RuntimeWarning
        )
        return _fallback_peak_normalize(audio, target_peak=-3.0, verbose=verbose)
    
    # Convert to numpy if torch tensor
    is_torch = False
    original_device = None
    original_dtype = None
    
    if hasattr(audio, 'cpu'):  # torch tensor
        is_torch = True
        original_device = audio.device
        original_dtype = audio.dtype
        audio_np = audio.cpu().numpy().astype(np.float32)
    else:
        audio_np = audio.astype(np.float32) if audio.dtype != np.float32 else audio.copy()
    
    # Handle shape: ensure (channels, samples) or (samples,) for mono
    original_shape = audio_np.shape
    if audio_np.ndim == 1:
        # Mono: (samples,) -> keep as is for pyloudnorm
        pass
    elif audio_np.ndim == 2:
        # Multi-channel: ensure shape is (samples, channels) for pyloudnorm
        if audio_np.shape[0] < audio_np.shape[1]:
            # Currently (channels, samples) -> transpose to (samples, channels)
            audio_np = audio_np.T
    else:
        raise ValueError(f"Unsupported audio shape: {original_shape}. Expected 1D or 2D array.")
    
    # Normalize audio range to [-1.0, 1.0] if it's in int16 range
    audio_max = np.abs(audio_np).max()
    if audio_max > 10.0:  # Likely int16 format
        audio_np = audio_np / 32767.0
        if verbose:
            print(f">> Converted audio from int16-like range (max: {audio_max:.0f}) to float32 [-1, 1]")
    
    try:
        # Measure current loudness
        meter = pyln.Meter(sample_rate)
        original_lufs = meter.integrated_loudness(audio_np)
        
        if verbose:
            print(f">> Original LUFS: {original_lufs:.2f} dB")
            print(f">> Target LUFS: {target_lufs:.2f} dB")
        
        # Check if audio is silent or extremely quiet
        if original_lufs < -70.0 or np.isnan(original_lufs) or np.isinf(original_lufs):
            warnings.warn(
                f"Audio is too quiet or silent (LUFS: {original_lufs:.2f}). "
                "Skipping normalization to avoid extreme amplification.",
                category=RuntimeWarning
            )
            # Restore original shape if needed
            if audio_np.ndim == 2 and original_shape[0] < original_shape[1]:
                audio_np = audio_np.T
            
            if is_torch:
                import torch
                audio_out = torch.from_numpy(audio_np).to(original_device).to(original_dtype)
            else:
                audio_out = audio_np
            
            return audio_out, {
                'original_lufs': original_lufs,
                'target_lufs': target_lufs,
                'gain_db': 0.0,
                'method': 'skipped_silent'
            }
        
        # Normalize to target LUFS
        normalized_audio = pyln.normalize.loudness(audio_np, original_lufs, target_lufs)
        
        # True peak limiting (prevent clipping)
        peak = pyln.normalize.peak(normalized_audio, true_peak_limit)
        if np.abs(normalized_audio).max() > np.abs(peak).max():
            # Peak limiting was applied
            normalized_audio = peak
            if verbose:
                print(f">> Applied true peak limiting to {true_peak_limit:.1f} dBFS")
        
        gain_db = target_lufs - original_lufs
        
        if verbose:
            final_lufs = meter.integrated_loudness(normalized_audio)
            print(f">> Applied gain: {gain_db:.2f} dB")
            print(f">> Final LUFS: {final_lufs:.2f} dB (target: {target_lufs:.2f} dB)")
            print(f">> Peak amplitude: {np.abs(normalized_audio).max():.3f}")
        
        # Restore original shape
        if audio_np.ndim == 2 and original_shape[0] < original_shape[1]:
            normalized_audio = normalized_audio.T
        
        # Convert back to original type
        if is_torch:
            import torch
            normalized_audio = torch.from_numpy(normalized_audio).to(original_device).to(original_dtype)
        
        return normalized_audio, {
            'original_lufs': float(original_lufs),
            'target_lufs': float(target_lufs),
            'gain_db': float(gain_db),
            'method': 'lufs_bs1770'
        }
    
    except Exception as e:
        warnings.warn(
            f"LUFS normalization failed: {e}. Falling back to peak normalization.",
            category=RuntimeWarning
        )
        # Restore original shape before fallback
        if audio_np.ndim == 2 and original_shape[0] < original_shape[1]:
            audio_np = audio_np.T
        
        if is_torch:
            import torch
            audio_fallback = torch.from_numpy(audio_np).to(original_device).to(original_dtype)
        else:
            audio_fallback = audio_np
        
        return _fallback_peak_normalize(audio_fallback, target_peak=-3.0, verbose=verbose)


def _fallback_peak_normalize(
    audio: Union[np.ndarray, "torch.Tensor"],
    target_peak: float = -3.0,
    verbose: bool = False
) -> Tuple[Union[np.ndarray, "torch.Tensor"], dict]:
    """
    Simple peak normalization fallback when LUFS normalization is unavailable.
    
    This is NOT perceptually accurate but prevents clipping and provides
    consistent peak levels.
    
    Args:
        audio: Input audio (numpy array or torch tensor)
        target_peak: Target peak level in dBFS (default: -3.0)
        verbose: Enable logging
    
    Returns:
        Tuple of (normalized_audio, metrics_dict)
    """
    is_torch = hasattr(audio, 'cpu')
    
    if is_torch:
        peak = audio.abs().max().item()
        if peak > 0:
            target_amplitude = 10 ** (target_peak / 20.0)
            gain = target_amplitude / peak
            normalized = audio * gain
        else:
            normalized = audio
            gain = 1.0
    else:
        peak = np.abs(audio).max()
        if peak > 0:
            target_amplitude = 10 ** (target_peak / 20.0)
            gain = target_amplitude / peak
            normalized = audio * gain
        else:
            normalized = audio
            gain = 1.0
    
    gain_db = 20 * np.log10(gain) if gain > 0 else 0.0
    
    if verbose:
        print(f">> Fallback: Peak normalization to {target_peak:.1f} dBFS")
        print(f">> Applied gain: {gain_db:.2f} dB")
    
    return normalized, {
        'original_lufs': None,
        'target_lufs': None,
        'gain_db': float(gain_db),
        'method': 'peak_fallback'
    }


def check_normalization_available() -> bool:
    """
    Check if LUFS normalization is available.
    
    Returns:
        True if pyloudnorm is installed, False otherwise
    """
    return PYLOUDNORM_AVAILABLE


def get_audio_lufs(
    audio: Union[np.ndarray, "torch.Tensor"],
    sample_rate: int
) -> Optional[float]:
    """
    Measure the integrated loudness (LUFS) of audio.
    
    Args:
        audio: Input audio as numpy array or torch tensor
        sample_rate: Audio sample rate in Hz
    
    Returns:
        LUFS value as float, or None if measurement fails
    
    Examples:
        >>> lufs = get_audio_lufs(wav_tensor, 24000)
        >>> print(f"Audio loudness: {lufs:.2f} LUFS")
    """
    if not PYLOUDNORM_AVAILABLE:
        warnings.warn("pyloudnorm not available. Cannot measure LUFS.", category=RuntimeWarning)
        return None
    
    try:
        # Convert to numpy if needed
        if hasattr(audio, 'cpu'):
            audio_np = audio.cpu().numpy().astype(np.float32)
        else:
            audio_np = audio.astype(np.float32) if audio.dtype != np.float32 else audio
        
        # Handle shape
        if audio_np.ndim == 2 and audio_np.shape[0] < audio_np.shape[1]:
            audio_np = audio_np.T
        
        # Normalize to [-1, 1] if needed
        audio_max = np.abs(audio_np).max()
        if audio_max > 10.0:
            audio_np = audio_np / 32767.0
        
        meter = pyln.Meter(sample_rate)
        lufs = meter.integrated_loudness(audio_np)
        
        return float(lufs) if not (np.isnan(lufs) or np.isinf(lufs)) else None
    
    except Exception as e:
        warnings.warn(f"Failed to measure LUFS: {e}", category=RuntimeWarning)
        return None
