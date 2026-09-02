# Forced Alignment Documentation

**Status**: ✅ Fully Implemented  
**Engine**: stable-whisper (stable-ts >= 2.19.1)  
**Model**: Whisper `small` (CPU-only)  
**Version**: 1.0

---

## Overview

The IndexTTS Worker implements **mandatory forced alignment** for all TTS synthesis jobs. After audio synthesis and time-stretching, the worker uses stable-whisper to align the input text with the generated audio, producing word-level timestamps for subtitle rendering, karaoke, and video editing applications.

**Key Design Principles:**
- **Mandatory**: Alignment runs on every job; failure fails the job
- **Post-synthesis**: Runs after time-stretching to ensure timestamps match the final delivered audio
- **CPU-only**: Whisper runs on CPU to avoid GPU contention with IndexTTS synthesis
- **Three outputs**: Raw JSON (debug), SRT (subtitles), Parsed JSON (uploaded to S3)

---

## Architecture

### Pipeline Integration

```
1. TTS Synthesis (IndexTTS)
   └─→ base_audio_path (ratio=1.0, cached)

2. Time-stretch (if ratio != 1.0)
   └─→ local_output (final delivered audio)

3. Forced Alignment ← YOU ARE HERE
   ├─ Input: local_output, text, language_hint
   ├─ Model: Whisper small (CPU)
   └─→ Outputs:
       ├─ {job_id}_raw_alignment.json  (kept on disk, NOT uploaded)
       ├─ {job_id}_alignment.srt       (kept on disk, NOT uploaded)
       └─ {job_id}_alignment.json      (uploaded to S3, then deleted)

4. Upload Audio + Parsed Alignment JSON to S3

5. Return job result with alignment_path
```

**Critical Ordering**: Alignment **must** run on `local_output` (post time-stretch), never on `base_audio_path`. This ensures timestamps match the uploaded audio exactly.

---

## Configuration

### Environment Variables

```bash
# Model configuration
TTS_ALIGNMENT_MODEL=small          # Whisper model size (default: small)
TTS_ALIGNMENT_DEVICE=cpu           # Device (must be cpu; never use mps on macOS)
TTS_ALIGNMENT_MODEL_DIR=~/.cache/whisper  # Model cache directory (optional)

# Retry and circuit breaker
TTS_ALIGNMENT_MAX_RETRIES=2
CIRCUIT_BREAKER_ALIGNMENT_FAILURE_THRESHOLD=3
CIRCUIT_BREAKER_ALIGNMENT_RESET_TIMEOUT=60
```

**Important**: Never set `TTS_ALIGNMENT_DEVICE=mps` on Apple Silicon — this causes float64 tensor conversion crashes. Always use `cpu`.

### Dependencies

Added to `pyproject.toml`:

```toml
dependencies = [
    "stable-ts",      # stable-ts >= 2.19.1; brings openai-whisper transitively
    "torch",          # explicit to allow macOS CPU-only resolution
    "torchaudio",     # explicit for the same reason
]
```

---

## Language Strategy (v1)

The worker uses a **majority-script strategy** for mixed Chinese/English text:

| Text Profile | Language Used | Alignment Quality Flag |
|--------------|---------------|------------------------|
| Pure Chinese (no Latin letters) | `zh` | `monolingual_zh` |
| Pure English (no CJK) | `en` | `monolingual_en` |
| Mixed (Latin > 40% of alphanumeric) | `en` | `mixed_fallback` |
| Mixed (Latin ≤ 40% of alphanumeric) | `zh` | `mixed_fallback` |

**Rationale**: Whisper alignment requires a single language per segment. The v1 strategy passes the entire text to Whisper with the majority language, flagging mixed text with `mixed_fallback` for downstream QA filtering.

**Future Enhancement (Phase 2)**: Per-segment alignment with script-based text segmentation for improved mixed-text accuracy.

---

## Output Format

### File Lifecycle

Three files are generated per job:

1. **`{job_id}_raw_alignment.json`** — Native stable-whisper output
   - Contains: Full Whisper segments, token probabilities, mel offsets
   - Lifecycle: **Retained on local disk** for debugging
   - Upload: **NOT uploaded** to S3

2. **`{job_id}_alignment.srt`** — SRT subtitle format
   - Contains: Word-level SRT timestamps
   - Lifecycle: **Retained on local disk** for subtitle transcoding
   - Upload: **NOT uploaded** to S3

3. **`{job_id}_alignment.json`** — Parsed JSON (v1 schema)
   - Contains: Distilled data for video rendering (segments, words, metadata)
   - Lifecycle: **Uploaded to S3**, then **deleted from local disk**
   - Upload: **YES** (only this file is uploaded)

### S3 Paths

```
Audio:          tts-audio/studio/{job_id}.mp3
Alignment JSON: tts-audio/studio/{job_id}.json   ← Derived from audio path
```

### JSON Schema (v1)

```json
{
  "version": "1.0",
  "job_id": "abc123",
  "engine": "stable-whisper",
  "engine_version": "2.19.1",
  "model": "small",
  "device": "cpu",
  "audio_duration_seconds": 12.34,
  "language_strategy": "monolingual_zh",
  "alignment_quality": "monolingual_zh",
  "source_text": "你好，世界。",
  "segments": [
    {
      "id": 0,
      "text": "你好，世界。",
      "language": "zh",
      "start": 0.0,
      "end": 12.34,
      "words": [
        {"word": "你", "start": 0.12, "end": 0.28, "probability": 0.98},
        {"word": "好", "start": 0.28, "end": 0.52, "probability": 0.97},
        {"word": "，", "start": 0.52, "end": 0.60, "probability": 0.99},
        {"word": "世", "start": 0.60, "end": 0.88, "probability": 0.96},
        {"word": "界", "start": 0.88, "end": 1.20, "probability": 0.98},
        {"word": "。", "start": 1.20, "end": 1.30, "probability": 0.99}
      ]
    }
  ],
  "words": [
    {"word": "你", "start": 0.12, "end": 0.28, "probability": 0.98},
    {"word": "好", "start": 0.28, "end": 0.52, "probability": 0.97}
  ],
  "alignment_duration_seconds": 1.87,
  "aligned_at": "2026-09-02T02:30:00+00:00"
}
```

**Field Descriptions**:
- `segments[]`: Per-language blocks (length 1 for monolingual jobs)
- `words[]`: Flattened global timeline for easy subtitle consumption
- `probability`: Whisper token confidence (0.0-1.0) — useful for QA filtering
- `alignment_quality`: `monolingual_zh`, `monolingual_en`, or `mixed_fallback`

---

## RabbitMQ Job Result

The worker extends the job result payload with alignment metadata:

```python
{
    "job_type": "studio",
    "job_id": "abc123",
    "status": "completed",
    "audio_path": "tts-audio/studio/abc123.mp3",
    "audio_duration_seconds": 12.34,
    "synthesis_duration_seconds": 5.21,
    "alignment_path": "tts-audio/studio/abc123.json",  # ← S3 key of parsed JSON
    "alignment_duration_seconds": 1.87,                # ← CPU alignment time
    "cache_hit": false,
    "retry_count": 0,
    "started_at": "2026-09-02T02:30:00+00:00",
    "completed_at": "2026-09-02T02:30:07+00:00"
}
```

**Note**: `subtitle_path` is **not** included — the SRT file exists only on local worker disk. Downstream consumers (e.g., Remotion) access alignment data exclusively via `alignment_path` (the parsed JSON in S3).

---

## Error Handling

Alignment is **mandatory**. Alignment failure fails the job.

### Error Codes

| Error | Code | Retryable? |
|-------|------|------------|
| Empty/whitespace text | `ALIGNMENT_INVALID_INPUT` | No — job fails immediately |
| Audio file missing | `ALIGNMENT_AUDIO_NOT_FOUND` | No |
| Alignment timeout / OOM | `ALIGNMENT_FAILED` | Yes (up to `TTS_ALIGNMENT_MAX_RETRIES`) |
| Circuit breaker open | `ALIGNMENT_CIRCUIT_OPEN` | Yes (after reset timeout) |

### Circuit Breaker

**Threshold**: 3 consecutive failures  
**Reset Timeout**: 60 seconds

When open, all alignment requests fail immediately with `ALIGNMENT_CIRCUIT_OPEN` until the timeout expires.

---

## Performance

### Expected CPU Overhead

| Audio Length | Alignment Time (Whisper small, CPU) |
|--------------|-------------------------------------|
| 10 seconds   | ~0.5-1.5 seconds                   |
| 60 seconds   | ~2-5 seconds                       |
| 5 minutes    | ~10-25 seconds                     |

### Memory Usage

- Whisper `small` on CPU: ~1-2 GB RAM during alignment
- No GPU memory used (alignment runs on CPU)
- Worker should have headroom above TTS GPU memory requirements

### Startup Cost

- First model load: ~2-5 seconds added to worker startup
- Model is loaded once at worker `__init__` and reused across jobs

---

## Normalization Mismatch Risk

**Issue**: IndexTTS internally normalizes text before synthesis (e.g., `100` → `一百`, `$50` → `fifty dollars`), but alignment receives the **raw input text**. When the phonetic output diverges significantly from the raw text, Whisper may produce incorrect word boundaries.

**Affected Text**: Digit-heavy, currency symbols, dates, abbreviations

**v1 Strategy**: Pass raw text to alignment; log warning when digits/currency/symbols detected

**Mitigation**: Downstream consumers can filter by `alignment_quality` or `probability` values

**Future Enhancement (Phase 3)**: Expose normalized text from `TextNormalizer` and pass it to alignment instead

---

## Implementation Status

### ✅ Phase 1 — Core (Completed)

- [x] Add `stable-ts` dependency to `pyproject.toml`
- [x] Implement `services/alignment.py` (monolingual path)
- [x] Integrate into `process_job()` after time-stretch, before upload
- [x] Upload parsed JSON sidecar to S3
- [x] Extend job result payload with `alignment_path`
- [x] Unit tests (`tests/test_alignment.py`) — 32 tests, all passing
- [x] Integration tests (`tests/pytest/test_tts_worker_alignment.py`)
- [x] Update `.env.example` with alignment configuration
- [x] Circuit breaker for alignment failures

### 🔄 Phase 2 — Bilingual (Not Started)

- [ ] Script-based text segmentation for mixed ZH/EN
- [ ] Per-segment alignment + timestamp merging
- [ ] `language_strategy` metadata in JSON output
- [ ] Mixed-text QA fixtures

### 🔄 Phase 3 — Hardening (Not Started)

- [ ] Alignment metrics dashboard
- [ ] Startup health check (dry-run alignment on silence fixture)
- [ ] DLQ / monitor alerts for `ALIGNMENT_*` error codes
- [ ] Expose normalized text from `TextNormalizer` for alignment

---

## Module Reference

### `services/alignment.py`

#### `class AlignmentService`

Singleton wrapper around stable-whisper for forced alignment.

**Methods**:

- `__init__(model_name, device, download_root)` — Initialize service
- `load_model()` — Load Whisper model (called once at worker startup)
- `align(audio_path, text, language_hint, job_id)` → `AlignmentResult` — Run forced alignment
- `align_to_files(job_id, audio_path, text, language_hint, output_dir)` → `(raw_json, srt, parsed_json)` — Align and write all three output files

#### `class AlignmentResult`

Parsed alignment result with JSON serialization.

**Attributes**:
- `job_id`, `whisper_language`, `alignment_quality`
- `audio_duration_seconds`, `source_text`
- `segments[]`, `words[]`
- `alignment_duration_seconds`, `aligned_at`, `engine_version`

**Methods**:
- `to_dict()` → `dict` — Serialize to v1 JSON schema

#### Helper Functions

- `detect_language_strategy(text, language_hint)` → `(language, quality)` — Determine Whisper language and quality flag
- `has_normalization_risk(text)` → `bool` — Detect digits/currency/symbols

---

## Testing

### Unit Tests

**File**: `tests/test_alignment.py`  
**Status**: ✅ 32 tests passing

**Coverage**:
- Language strategy detection (pure ZH, pure EN, mixed, edge cases)
- Normalization risk detection (digits, currency, clean text)
- JSON schema serialization round-trip
- AlignmentService validation (empty text, missing audio, device override)
- Audio duration calculation

**Run**:
```bash
uv run pytest tests/test_alignment.py -v
```

### Integration Tests

**File**: `tests/pytest/test_tts_worker_alignment.py`  
**Status**: ✅ Implemented (requires full TTS setup on Windows/Linux)

**Coverage**:
- Alignment called with `local_output` after time-stretch
- Failure paths return correct error codes
- Result dict contains `alignment_path` and `alignment_duration_seconds`
- Only parsed JSON is uploaded (not SRT or raw JSON)
- Circuit breaker open returns `ALIGNMENT_CIRCUIT_OPEN`

**Run** (Windows/Linux with GPU):
```bash
# Requires conda environment with IndexTTS dependencies
conda activate index-tts
python -m pytest tests/pytest/test_tts_worker_alignment.py -v
```

---

## Troubleshooting

### Model Download Issues

**Symptom**: Worker hangs on first alignment

**Cause**: Whisper model downloading from HuggingFace

**Solution**: Pre-download model or set persistent cache directory

```bash
# Option 1: Pre-download model
python -c "import whisper; whisper.load_model('small')"

# Option 2: Set persistent cache (in .env)
TTS_ALIGNMENT_MODEL_DIR=/opt/models/whisper
```

### macOS MPS Crash

**Symptom**: `RuntimeError: Placeholder storage has not been allocated on MPS device!`

**Cause**: `TTS_ALIGNMENT_DEVICE=mps` on Apple Silicon

**Solution**: Always use `cpu` device on macOS

```bash
# In .env
TTS_ALIGNMENT_DEVICE=cpu  # Never use mps
```

### Missing Words in Alignment

**Symptom**: Words missing or merged in `words[]` array

**Possible Causes**:
1. **Normalization mismatch**: Input text contains digits/currency
2. **Mixed language**: Text has both ZH and EN (v1 uses fallback strategy)
3. **Low audio quality**: Background noise or unclear speech

**Mitigation**:
- Check `alignment_quality` field in JSON output
- Filter by `probability` threshold (e.g., `>= 0.8`)
- For mixed text, wait for Phase 2 per-segment alignment

---

## API Integration Example (Remotion)

```typescript
// Remotion composition: Fetch alignment data from S3
import { useEffect, useState } from 'react';

interface AlignmentWord {
  word: string;
  start: number;
  end: number;
  probability: number;
}

interface AlignmentData {
  job_id: string;
  words: AlignmentWord[];
  alignment_quality: string;
}

export const TtsVideo = ({ job }: { job: JobResult }) => {
  const [alignment, setAlignment] = useState<AlignmentData | null>(null);

  useEffect(() => {
    fetch(job.alignment_path)  // S3 presigned URL or CDN path
      .then(res => res.json())
      .then(data => setAlignment(data));
  }, [job.alignment_path]);

  if (!alignment) return <div>Loading...</div>;

  // Filter low-confidence words (optional QA)
  const highConfWords = alignment.words.filter(w => w.probability >= 0.8);

  // Render karaoke-style subtitles
  return (
    <Sequence>
      {highConfWords.map((word, i) => (
        <Sequence
          key={i}
          from={Math.floor(word.start * 30)}  // Convert to frame number
          durationInFrames={Math.floor((word.end - word.start) * 30)}
        >
          <Word text={word.word} />
        </Sequence>
      ))}
    </Sequence>
  );
};
```

---

## References

- **Implementation Plan**: [`docs/STABLE_WHISPER_ALIGNMENT_PLAN.md`](./STABLE_WHISPER_ALIGNMENT_PLAN.md) (original design document)
- **stable-ts Repository**: https://github.com/jianfch/stable-ts
- **Whisper Paper**: https://arxiv.org/abs/2212.04356
- **LUFS Standard (ITU-R BS.1770-4)**: https://www.itu.int/rec/R-REC-BS.1770/

---

**Last Updated**: 2026-09-02  
**Author**: IndexTTS Worker Team  
**Version**: 1.0
