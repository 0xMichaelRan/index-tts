# TTS Inference Method Control

## Overview

The IndexTTS worker now supports configurable inference methods for Windows/Linux systems, allowing you to choose between fast batched inference (`infer_fast()`) and sequential inference (`infer()`) based on your hardware and workload requirements.

## Configuration

### Environment Variable

```bash
TTS_USE_FAST_INFERENCE=true   # Default: Use infer_fast() (fast, higher memory)
# TTS_USE_FAST_INFERENCE=false  # Use infer() (slower, lower memory, more stable)
```

Add this to your `.env` file to control the inference method.

## Inference Methods Comparison

### `infer_fast()` (Default)

**When to use:**
- Production environments with adequate GPU memory (8GB+ VRAM)
- Processing long text with multiple sentences
- When speed is critical (2-10x faster)
- High-throughput job queues

**Characteristics:**
- ✅ 2-10x faster for multi-sentence text
- ✅ Sentence batching and bucketing
- ✅ Optimized for long text
- ❌ Higher GPU memory usage
- ❌ More complex batch processing logic

**Performance:**
```
Single sentence:     ~2s (similar to infer)
10 sentences:        ~5s (vs ~15s with infer)
50 sentences:        ~15s (vs ~60s with infer)
```

### `infer()` (Fallback)

**When to use:**
- Memory-constrained environments (4GB VRAM)
- Debugging inference issues
- Single-sentence synthesis
- When stability is more important than speed

**Characteristics:**
- ✅ Lower GPU memory usage
- ✅ More predictable behavior
- ✅ Simpler sequential processing
- ❌ Slower for long text (2-10x slower)
- ❌ No batching optimization

**Performance:**
```
Single sentence:     ~2s (similar to infer_fast)
10 sentences:        ~15s (vs ~5s with infer_fast)
50 sentences:        ~60s (vs ~15s with infer_fast)
```

## Platform Behavior

### Windows/Linux (GPU Inference)
- Respects `TTS_USE_FAST_INFERENCE` setting
- Both methods available
- Default: `infer_fast()` for optimal performance

### macOS (Native TTS)
- Always uses `infer()` with native macOS TTS
- `TTS_USE_FAST_INFERENCE` setting ignored
- No GPU acceleration available

## Implementation Details

### Code Location

The inference method selection is implemented in:
- **Configuration**: `services/tts_worker.py` (line ~170)
- **Synthesis logic**: `services/tts_worker.py` (`_synthesize_audio()` method, line ~805)

### Configuration Loading

```python
# In __init__:
self.use_fast_inference = os.getenv("TTS_USE_FAST_INFERENCE", "true").lower() == "true"

if self.platform != "Darwin":
    inference_method = "infer_fast()" if self.use_fast_inference else "infer()"
    logger.info(f"TTS inference method: {inference_method}")
```

### Runtime Selection

```python
# In _synthesize_audio():
if self.use_fast_inference:
    self.tts.infer_fast(
        audio_prompt=audio_prompt,
        text=text,
        output_path=output_path,
        ratio=1.0,
    )
else:
    self.tts.infer(
        audio_prompt=audio_prompt,
        text=text,
        output_path=output_path,
        ratio=1.0,
    )
```

## Startup Log Output

The worker logs the selected inference method during startup:

```
18:30:45 [INFO    ] TTS synthesis cache: ENABLED
18:30:45 [INFO    ]   Max entries: 10000
18:30:45 [INFO    ]   Eviction threshold: 9000
18:30:45 [INFO    ]   Cache directory: outputs/tts_cache
18:30:45 [INFO    ] TTS inference method: infer_fast()  ← Shows selected method
18:30:45 [INFO    ] Audio normalization: ENABLED
```

## Troubleshooting

### Out of Memory Errors

If you encounter CUDA out-of-memory errors:

```
RuntimeError: CUDA out of memory. Tried to allocate 2.50 GiB
```

**Solution**: Switch to `infer()` mode:
```bash
TTS_USE_FAST_INFERENCE=false
```

### Slow Inference

If inference is too slow for your workload:

**Solution**: Switch to `infer_fast()` mode (if not already enabled):
```bash
TTS_USE_FAST_INFERENCE=true
```

### Audio Quality Issues

If you notice audio quality differences between methods:

**Note**: Both methods use the same underlying models and should produce identical audio quality. Any differences are likely due to:
- Different batch processing order
- Numerical precision differences in batched operations

**Solution**: Use `infer()` for consistent sequential processing.

## Migration Guide

### Existing Deployments

If you're upgrading from a version without this feature:

1. **No action required**: Default behavior remains `infer_fast()` (existing behavior)
2. **Optional**: Add `TTS_USE_FAST_INFERENCE=true` to `.env` for explicit configuration
3. **Memory issues**: Set `TTS_USE_FAST_INFERENCE=false` if experiencing OOM errors

### Performance Testing

To compare performance for your workload:

1. Process test jobs with `TTS_USE_FAST_INFERENCE=true`
2. Record synthesis times from worker logs
3. Process same jobs with `TTS_USE_FAST_INFERENCE=false`
4. Compare total synthesis time and memory usage

## See Also

- **AGENTS.md** - Main worker documentation
- **indextts/infer.py** - Inference method implementations
- **docs/CACHE_IMPLEMENTATION_SUMMARY.md** - Synthesis caching details
