# Voice Caching Fix

## Problem

The TTS worker was regenerating the `cond_mel` (conditional mel-spectrogram) for the same voice on every job, even when using the same audio prompt from S3.

### Root Cause

The IndexTTS engine caches voices based on the `audio_prompt` path parameter. However, the worker was downloading voices to **different local temporary paths** for each job:

- Job 14: `outputs\temp\14\english.wav`
- Job 15: `outputs\temp\15\english.wav`

Even though both files have identical content (downloaded from the same S3 path `voice-recordings/user/312/english.wav`), the different local paths caused cache misses in `infer_fast`.

### Observed Behavior (Before Fix)

```
[JOB 14] Processing TTS request (type: studio, language: en, ratio: 0.7)
>> 声音没有缓存，重新生成 cond_mel for outputs\temp\14\english.wav

[JOB 15] Processing TTS request (type: studio, language: en, ratio: 1.3)
>> 声音没有缓存，重新生成 cond_mel for outputs\temp\15\english.wav
```

## Solution

Implement a **two-level caching strategy**:

1. **Worker level**: Track voices by S3 path to detect when the same voice is reused
2. **IndexTTS level**: Temporarily override cache key to match local paths for `infer_fast` comparison

### Implementation Details

The fix manipulates the `self.tts.cache_audio_prompt` value at different stages:

**For the first job with a voice:**
```python
# cache_audio_prompt starts as None
# Don't set it before infer_fast, so audio loads normally
self.tts.infer_fast(audio_prompt=local_path, ...)  # Generates cond_mel
# After inference, store S3 path as cache key
self.tts.cache_audio_prompt = s3_path  # e.g., "voice-recordings/user/312/english.wav"
```

**For subsequent jobs with the same voice:**
```python
# Check if S3 path matches cached S3 path
if self.tts.cache_audio_prompt == audio_prompt_s3_path:  # TRUE - same voice!
    # Override cache key to current local path BEFORE calling infer_fast
    self.tts.cache_audio_prompt = local_path  # e.g., "outputs\temp\18\english.wav"
    
# Now infer_fast's comparison succeeds:
# if self.cache_audio_prompt != audio_prompt:  → FALSE (both are same local path)
# So it reuses cached cond_mel ✅
self.tts.infer_fast(audio_prompt=local_path, ...)

# After inference, restore S3 path for next job
self.tts.cache_audio_prompt = s3_path
```

### Code Changes

**File**: `services/tts_worker.py`

#### Cache Logic in `_synthesize_audio`

```python
if audio_prompt_s3_path:
    # Check if this is the same voice as previous job (by S3 path)
    is_same_voice = (self.tts.cache_audio_prompt == audio_prompt_s3_path)
    
    if not is_same_voice:
        # New voice - clear cache to force audio load
        logger.info(f"[JOB {job_id}] Loading new voice (S3: {audio_prompt_s3_path})")
        self.tts.cache_audio_prompt = None
        self.tts.cache_cond_mel = None
    else:
        # Same voice - override cache key to local path so infer_fast comparison matches
        logger.info(f"[JOB {job_id}] Reusing cached voice (S3: {audio_prompt_s3_path})")
        self.tts.cache_audio_prompt = audio_prompt  # Local path
    
    # Run inference (either loads or reuses based on cache state above)
    self.tts.infer_fast(audio_prompt=audio_prompt, ...)
    
    # Store S3 path as cache key for next job's comparison
    self.tts.cache_audio_prompt = audio_prompt_s3_path
```

## Expected Behavior After Fix

```
[JOB 17] Processing TTS request (type: studio, language: en, ratio: 0.7)
[JOB 17] Loading new voice (S3: voice-recordings/user/312/english.wav)
>> 声音没有缓存，重新生成 cond_mel for outputs\temp\17\english.wav

[JOB 18] Processing TTS request (type: studio, language: en, ratio: 2.0)
[JOB 18] Reusing cached voice (S3: voice-recordings/user/312/english.wav)
>> 找到了缓存的 cond_mel for outputs\temp\18\english.wav, shape: [1, 80, xxx]
```

## Benefits

1. **Performance**: Eliminates redundant `cond_mel` generation (saves ~2-5 seconds per job)
2. **GPU Memory**: Reduces VRAM usage by reusing cached mel-spectrograms
3. **Cost**: Lower compute costs for sequential jobs with same voice
4. **Scalability**: Better throughput for batch processing with voice reuse

## Testing

Test the fix by:

1. Restart the worker: `python services/tts_worker.py` (Windows with conda activated)
2. Submit multiple TTS jobs with the same voice but different texts/speeds
3. Check logs for cache behavior:
   - First job: `Loading new voice` + `声音没有缓存，重新生成 cond_mel`
   - Second job: `Reusing cached voice` + `找到了缓存的 cond_mel`

## Backwards Compatibility

- Works with existing job format (S3 path is already in `audio_prompt_path`)
- Graceful fallback: If S3 path is not provided, caching is disabled with warning
- No changes required to backend or queue messages

## Technical Notes

- Cache is worker-instance scoped (cleared on worker restart)
- Cache key manipulation happens before/after `infer_fast`, not inside it
- The `cond_mel` tensor is preserved across jobs (stored in `self.tts.cache_cond_mel`)
- For multi-worker deployments, each worker maintains its own cache
- Consider adding persistent caching (Redis/disk) for cross-worker voice reuse in future

## Troubleshooting

If caching still doesn't work:

1. Check that `audio_prompt_path` is passed correctly from backend
2. Verify S3 path format (e.g., `voice-recordings/user/312/english.wav`)
3. Look for `[JOB X] Loading new voice` vs `Reusing cached voice` in logs
4. Check `infer_fast` output: `声音没有缓存` vs `找到了缓存的 cond_mel`
