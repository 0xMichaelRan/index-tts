# Audio Loudness Normalization Fix

## Issue Summary

After implementing LUFS loudness normalization in commit `e0fc5dc1121815ddee92a46ac20a379238437e1f`, synthesized TTS audio became fully muted (silent).

## Root Cause

The issue was caused by an **audio range conversion bug** in the normalization pipeline:

1. **In `indextts/infer.py`**: TTS audio is scaled to int16 range after BigVGAN generation:
   ```python
   wav = torch.clamp(32767 * wav, -32767.0, 32767.0)
   ```

2. **In `audio_normalization.py`**: The normalization function correctly detected this and converted to [-1, 1] range:
   ```python
   if audio_max > 10.0:  # Likely int16 format
       audio_np = audio_np / 32767.0  # Convert to [-1, 1]
   ```

3. **The Bug**: After normalization (which keeps audio in [-1, 1] range), the audio was returned as float32 in [-1, 1] range, but then directly converted to int16:
   ```python
   torchaudio.save(output_path, wav.type(torch.int16), sampling_rate)
   ```
   
   This caused **truncation**: values like `0.5` became `0`, effectively muting the entire audio.

## The Fix

Modified `indextts/utils/audio_normalization.py` to **preserve the input audio range**:

### Changes Made

1. **Track if input was in int16 range**:
   ```python
   was_int16_range = audio_max > 10.0
   ```

2. **Scale back to int16 range after normalization** (if input was in that range):
   ```python
   if was_int16_range:
       normalized_audio = np.clip(normalized_audio * 32767.0, -32767.0, 32767.0)
   ```

3. **Applied fix to all code paths**:
   - Main LUFS normalization path
   - Silent audio handling path
   - Error fallback path
   - Peak normalization fallback (when pyloudnorm unavailable)

## Testing

### Before Fix
```python
wav = torch.randn(1, 24000) * 16000.0  # int16 range
normalized, _ = normalize_loudness(wav, 24000)
wav_int16 = normalized.type(torch.int16)
print(wav_int16.max())  # Output: 0 or ±1 (MUTED!)
```

### After Fix
```python
wav = torch.randn(1, 24000) * 16000.0  # int16 range
normalized, _ = normalize_loudness(wav, 24000)
wav_int16 = normalized.type(torch.int16)
print(wav_int16.max())  # Output: ~16000 (PRESERVED!)
```

### Test Coverage

Added comprehensive test in `tests/test_audio_normalization.py`:

```python
def test_int16_range_audio(self, sample_audio_numpy):
    """Test normalization with audio in int16 range."""
    audio, sample_rate = sample_audio_numpy
    
    # Scale to int16-like range (simulating actual TTS output)
    audio_int16_like = audio * 16000
    
    normalized, metrics = normalize_loudness(
        audio=audio_int16_like,
        sample_rate=sample_rate,
        target_lufs=-16.0,
        enable_normalization=True,
        verbose=False
    )
    
    # CRITICAL: Output should remain in int16 range
    assert np.abs(normalized).max() > 10.0
    
    # Verify audio survives int16 conversion
    as_int16 = torch.from_numpy(normalized).type(torch.int16)
    assert as_int16.abs().max() > 100
```

## Impact

- ✅ **Audio is no longer muted** after normalization
- ✅ **Loudness normalization works correctly** with both pyloudnorm and fallback
- ✅ **No changes required** in `indextts/infer.py` or `services/tts_worker.py`
- ✅ **Backward compatible** - audio already in [-1, 1] range is handled correctly

## Files Modified

1. `indextts/utils/audio_normalization.py` - Core fix
2. `tests/test_audio_normalization.py` - Enhanced test coverage

## Verification

Run the test suite to verify the fix:

```bash
# Test the specific int16 range handling
conda activate index-tts
python -m pytest tests/test_audio_normalization.py::TestNormalizeLoudness::test_int16_range_audio -v

# Run all normalization tests
python -m pytest tests/test_audio_normalization.py -v
```

All tests should pass with pyloudnorm installed.

## Related Documentation

- [AGENTS.md](../AGENTS.md) - Audio Loudness Normalization section
- [Audio Normalization Module](../indextts/utils/audio_normalization.py)
- [Test Suite](../tests/test_audio_normalization.py)
