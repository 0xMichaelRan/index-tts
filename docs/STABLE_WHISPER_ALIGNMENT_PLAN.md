# Stable-Whisper Forced Alignment — Implementation Plan

**Status:** Plan (not implemented)  
**Date:** September 2026  
**Scope:** Mandatory post-synthesis alignment step in `services/tts_worker.py`

---

## 1. Background

IndexTTS (`indextts/infer.py`) produces audio only. It does not export text-to-audio alignment (word, sentence, or phoneme timestamps). Internal mel-code lengths are not mapped back to BPE or display text and are not exposed by the inference API.

Downstream consumers (subtitles, karaoke, video editors, caption pipelines) need timestamps aligned to the **final delivered audio**. This plan adds a **mandatory** worker step using [stable-ts](https://github.com/jianfch/stable-ts) (`import stable_whisper`) to perform **forced alignment** of the known input text against the synthesized waveform.

### Design constraints (from product requirements)

| Constraint | Decision |
|------------|----------|
| Alignment is optional? | **No — mandatory for every job** |
| Model size | **`small`** (Whisper small) |
| Device | **CPU only** — do not load on GPU; TTS already uses GPU |
| Bilingual text (ZH/EN) | Segment-by-script strategy (see §5) |
| Speed ratio `ratio != 1.0` | Align on **final time-stretched audio**, not base `ratio=1.0` cache audio |

---

## 2. Goals and non-goals

### Goals

1. Every completed worker job returns word-level (and sentence-level) timestamps matching the uploaded audio file.
2. Alignment runs on the exact bytes uploaded to S3 (post time-stretch, post normalization).
3. CPU inference keeps GPU memory reserved for IndexTTS.
4. ZH, EN, and mixed ZH/EN text are handled with a deterministic language strategy.
5. Alignment artifacts are stored alongside audio in the output bucket.

### Non-goals (v1)

- Phoneme-level alignment (MFA / wav2vec2 phoneme models).
- Caching alignment results in the synthesis cache (timestamps depend on `ratio`; see §6).
- Alignment inside `indextts/infer.py` (keep synthesis and alignment decoupled).
- Real-time / streaming alignment.
- macOS native TTS path (`indextts/macos_tts.py`) — out of scope unless macOS worker is later required to support alignment.

---

## 3. Current worker pipeline

```
RabbitMQ job
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 1. Cache lookup (text + voice)                          │
│    └─ hit → copy base audio                             │
│    └─ miss → download prompt → synthesize (ratio=1.0)   │
│              → store in cache                             │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Time-stretch (if ratio != 1.0)                       │
│    local_output = stretched or base audio               │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Upload audio to S3 (output bucket)                   │
└─────────────────────────────────────────────────────────┘
    │
    ▼
 Return job result JSON
```

### Proposed pipeline (alignment inserted before upload)

```
... synthesis + time-stretch → local_output ...
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ NEW Step 3: Forced alignment (stable-whisper, CPU)      │
│    input:  local_output (final WAV)                     │
│            text (job input, unchanged)                  │
│            language hint (job.language)                 │
│    output: local_alignment.json                         │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Step 4: Upload audio + alignment JSON to S3             │
└─────────────────────────────────────────────────────────┘
    │
    ▼
 Return job result JSON (includes alignment_path)
```

**Critical ordering rule:** Alignment must run **after** `_apply_ratio_to_cached_audio()` and **before** S3 upload. Never align `base_audio_path` when `ratio != 1.0`.

---

## 4. Technology choice: stable-ts (`stable_whisper`)

### Package

- **PyPI:** `stable-ts`
- **Import:** `import stable_whisper`
- **API:** `model.align(audio_path, text, language=..., ...)` — forced alignment with known transcript (not open-ended transcription).

### Why stable-ts

- Designed for reliable word-level timestamps on top of Whisper.
- Supports alignment mode with a provided transcript (ideal for TTS where text is known).
- Runs on CPU with the `small` checkpoint.
- No changes to IndexTTS weights required.

### Model configuration

```python
import stable_whisper

model = stable_whisper.load_model(
    "small",
    device="cpu",
    cpu_preload=True,  # preload weights on CPU at worker startup
)
```

| Setting | Value | Rationale |
|---------|-------|-----------|
| Model | `small` | Balance of accuracy vs CPU latency (~500 MB weights) |
| Device | `cpu` | Avoid GPU contention with IndexTTS; worker is GPU-saturated during synthesis |
| Load timing | Worker `__init__` (singleton) | Amortize load cost across jobs; ~2–5 s one-time startup |
| FP16 | Off on CPU | Use default float32 on CPU |

### Dependency addition (`pyproject.toml`)

Add to main dependencies (alignment is mandatory, not optional):

```toml
"stable-ts>=2.17.0",
```

`stable-ts` pulls in `openai-whisper`, `torch`, and `torchaudio`. Torch is already present in the CUDA extra; document that CPU-only workers still need torch for alignment even on macOS dev machines.

---

## 5. ZH/EN language strategy

IndexTTS normalizes and tokenizes mixed Chinese/English in a single pass (`TextNormalizer` + BPE). Whisper alignment, however, needs a **per-segment language** for best results. A single `language="en"` or `language="zh"` on mixed text degrades alignment quality.

### Recommended strategy: script-based segmentation

Split the input `text` into ordered segments where each segment is either predominantly CJK or predominantly Latin, then align each segment against the corresponding audio slice.

```
Input:  "你好 world，这是 test。"
         │      │      │      │
         ▼      ▼      ▼      ▼
Seg 0: "你好"           lang=zh
Seg 1: " world，"       lang=en  (Latin + punctuation attached to EN run)
Seg 2: "这是"           lang=zh
Seg 3: " test。"       lang=en
```

#### Segmentation algorithm (v1)

```python
import re

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+")
LATIN_RE = re.compile(r"[A-Za-z0-9]+")

def segment_text_by_script(text: str) -> list[dict]:
    """
    Returns ordered segments: [{"text": str, "language": "zh"|"en"}, ...]
    Punctuation and whitespace attach to the preceding segment.
    """
```

Rules:

1. **CJK runs** → `language="zh"`.
2. **Latin alphanumeric runs** → `language="en"`.
3. **Punctuation / spaces** → append to the previous segment; if at start, attach to the next segment.
4. **Numbers embedded in Chinese** (e.g. `第3章`) → stay in the CJK segment; Whisper zh handles digits reasonably.
5. **Job `language` hint** — used only when the entire text is monolingual (no script from the other language detected). Fallback when segmentation yields a single segment with ambiguous content.

#### Alignment flow for mixed text

```
1. segments = segment_text_by_script(text)
2. If len(segments) == 1:
       result = model.align(audio, segments[0].text, language=segments[0].language)
   Else:
       # Sequential proportional split (v1 — see §5.1 for limitations)
       For each segment i:
           audio_slice = slice_audio_by_prior_segments(...)
           partial = model.align(audio_slice, segment.text, language=segment.language)
           offset partial timestamps by slice start time
       Merge into unified timeline
3. Emit words[] with global start/end
```

#### §5.1 v1 limitation: multi-segment audio slicing

Whisper align does not tell us where segment *i* starts in the audio before alignment. For v1, use a **two-pass proportional heuristic**:

1. **Pass A:** Align full text as one block using `language` from job hint (or majority script vote) to get coarse word timings.
2. **Pass B:** For each script segment, find the audio time window that covers that segment's characters (by matching aligned words to segment text), re-align within that window with the correct per-segment language.

If Pass B is too complex for v1, ship **monolingual path only** first (§10 phased rollout) and add mixed-text refinement in v1.1.

#### Simpler v1 fallback (recommended for initial implementation)

| Text profile | Strategy |
|--------------|----------|
| `language=zh`, no Latin letters | `model.align(audio, text, language="zh")` |
| `language=en`, no CJK | `model.align(audio, text, language="en")` |
| Mixed ZH/EN | `model.align(audio, text, language="zh")` with `regroup=False`; validate word coverage; if Latin words missing, retry with per-segment slicing (Pass B) |

Document known mixed-text accuracy risk in job metadata (`alignment_quality: "mixed_fallback"`).

---

## 6. Interaction with synthesis cache and `ratio`

| Scenario | Audio aligned | Cache stores alignment? |
|----------|---------------|-------------------------|
| Cache miss, `ratio=1.0` | `base_audio_path` | **No** |
| Cache miss, `ratio=1.5` | Time-stretched copy | **No** |
| Cache hit, `ratio=1.0` | Copied base audio | **No** |
| Cache hit, `ratio=0.8` | Time-stretched copy | **No** |

**Rationale:** Timestamps are valid only for a specific waveform duration. Time-stretching changes segment durations non-uniformly in perceptual terms; aligning the final stretched file avoids timestamp drift.

Alignment is always computed fresh per job. Expected CPU cost: ~1–4 s per minute of audio on `small`/CPU (benchmark during implementation).

---

## 7. Output format

### Local file

`outputs/tts_output/{job_id}/{job_id}_alignment.json`

### S3 path

Mirror audio path with `.json` suffix:

```
Audio:     tts-audio/studio/{job_id}.mp3
Alignment: tts-audio/studio/{job_id}.json
```

If audio is `.wav`, alignment uses the same stem. Derive from `output_path_template`:

```python
alignment_s3_path = output_s3_path.rsplit(".", 1)[0] + ".json"
```

### JSON schema (v1)

```json
{
  "version": "1.0",
  "job_id": "abc123",
  "engine": "stable-whisper",
  "model": "small",
  "device": "cpu",
  "audio_duration_seconds": 12.34,
  "language_strategy": "monolingual_zh",
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
        {"word": "好", "start": 0.28, "end": 0.52, "probability": 0.97}
      ]
    }
  ],
  "words": [
    {"word": "你", "start": 0.12, "end": 0.28, "probability": 0.98}
  ],
  "alignment_duration_seconds": 1.87,
  "aligned_at": "2026-09-01T02:30:00+08:00"
}
```

- **`segments`:** Per script/language block (length 1 for monolingual jobs).
- **`words`:** Flattened global timeline for easy subtitle consumption.
- **`probability`:** Whisper token confidence; useful for QA filtering.

### Worker result payload extension

Add to `process_job()` success dict:

```python
{
    ...
    "alignment_path": "tts-audio/studio/{job_id}.json",
    "alignment_duration_seconds": 1.87,
}
```

---

## 8. Module design

### New file: `services/alignment.py`

Responsibilities:

- Load and hold the singleton `stable_whisper` model (CPU, `small`).
- `align_audio(audio_path, text, language_hint) -> AlignmentResult`
- Script segmentation utilities.
- JSON serialization.
- Timing metrics.

```python
class AlignmentService:
    def __init__(self, model_name: str = "small", device: str = "cpu"): ...

    def align(
        self,
        audio_path: str,
        text: str,
        language_hint: str | None = None,
    ) -> AlignmentResult: ...
```

### Changes to `services/tts_worker.py`

1. **`__init__`:** Instantiate `AlignmentService` after TTS engine init.
2. **New circuit breaker:** `ALIGNMENT` (failure_threshold=3, reset_timeout=60).
3. **`process_job()`:** After `local_output` is finalized, call alignment; upload JSON; extend result.
4. **New helpers:**
   - `_align_audio(job_id, local_output, text, language) -> str` → local JSON path
   - `_upload_alignment(job_id, local_json, remote_path) -> str`

### Changes to `services/idempotent_upload.py`

Reuse `IdempotentUploader.upload_with_retry()` for the JSON sidecar (same integrity semantics as audio).

---

## 9. Configuration

Add to `.env.example`:

```bash
# ============================
# Forced Alignment (stable-whisper)
# ============================

# Model name (default: small)
TTS_ALIGNMENT_MODEL=small

# Device for alignment (must be cpu — GPU reserved for TTS)
TTS_ALIGNMENT_DEVICE=cpu

# Max alignment retries per job (transient failures)
TTS_ALIGNMENT_MAX_RETRIES=2

# Circuit breaker
CIRCUIT_BREAKER_ALIGNMENT_FAILURE_THRESHOLD=3
CIRCUIT_BREAKER_ALIGNMENT_RESET_TIMEOUT=60
```

Alignment is **always on** (no `TTS_ALIGNMENT_ENABLED` flag) per mandatory requirement.

---

## 10. Error handling

Alignment is mandatory: **alignment failure fails the job** (same as S3 upload failure).

| Error | `error_code` | Retryable? |
|-------|--------------|------------|
| Model load failure at startup | Worker fails to start | No — fix deployment |
| Alignment timeout / OOM on CPU | `ALIGNMENT_FAILED` | Yes (up to `TTS_ALIGNMENT_MAX_RETRIES`) |
| Circuit breaker open | `ALIGNMENT_CIRCUIT_OPEN` | Yes (after reset timeout) |
| Empty text | Skip alignment? **No** — fail with `ALIGNMENT_INVALID_INPUT` | No |
| Audio file missing | `ALIGNMENT_AUDIO_NOT_FOUND` | No |

Logging:

```
[JOB abc123] Running forced alignment (model=small, device=cpu, lang=zh)...
[JOB abc123] Alignment complete: 42 words, 1.9s
```

Startup log (alongside TTS / S3 summary):

```
Alignment: stable-whisper small on cpu (mandatory)
```

---

## 11. Performance and resource impact

### Expected overhead (to benchmark)

| Audio length | Approx. CPU alignment time (`small`) |
|--------------|--------------------------------------|
| 10 s | 0.5–1.5 s |
| 60 s | 2–5 s |
| 5 min | 10–25 s |

### Memory

- Whisper `small` on CPU: ~1–2 GB RAM during alignment.
- Worker should run on a host with headroom above TTS GPU memory (alignment uses CPU RAM only).

### Concurrency

- Worker processes one job at a time (`prefetch_count=1`).
- Alignment runs sequentially after synthesis — no additional parallelism needed in v1.

### Startup cost

- First model load: ~2–5 s added to worker startup.
- Consider logging model load time in the STARTUP section.

---

## 12. Testing plan

### Unit tests (`tests/test_alignment.py`)

- `segment_text_by_script()` — ZH only, EN only, mixed, punctuation edge cases.
- JSON schema serialization round-trip.
- Language hint fallback logic.

### Integration tests (`tests/test_alignment_integration.py`)

- Align a short known WAV fixture with known text; assert word count and end time ≈ duration.
- Mixed-text fixture (if v1 supports it).
- Verify alignment runs on time-stretched audio (ratio 1.5 changes duration vs ratio 1.0).

### Worker tests (`tests/pytest/test_tts_worker_alignment.py`)

- Mock `AlignmentService.align()` — verify called with `local_output` after stretch.
- Mock failure — job returns `ALIGNMENT_FAILED`.
- Result dict includes `alignment_path`.

### Manual QA checklist

- [ ] Monolingual Chinese studio job → valid ZH word timestamps.
- [ ] Monolingual English playground job → valid EN word timestamps.
- [ ] Mixed ZH/EN job → words cover full text (no missing Latin or CJK).
- [ ] `ratio=1.5` job → timestamps match slowed/sped audio in a video editor.
- [ ] Cache hit + `ratio != 1.0` → alignment on stretched copy, not cached base.
- [ ] Worker restart → model reloads on CPU without CUDA OOM.

---

## 13. Implementation phases

### Phase 1 — Core (MVP)

- [ ] Add `stable-ts` dependency.
- [ ] Implement `services/alignment.py` (monolingual path only).
- [ ] Integrate into `process_job()` after time-stretch, before upload.
- [ ] Upload JSON sidecar to S3.
- [ ] Extend job result payload.
- [ ] Unit + integration tests.
- [ ] Update `.env.example`, `docs/CONFIGURATION.md`, `AGENTS.md`.

### Phase 2 — Bilingual

- [ ] Script segmentation for mixed ZH/EN.
- [ ] Per-segment align + timestamp merge.
- [ ] `language_strategy` metadata in JSON output.
- [ ] Mixed-text QA fixtures.

### Phase 3 — Hardening

- [ ] Alignment circuit breaker + metrics.
- [ ] Startup health check (dry-run align on 1 s silence fixture).
- [ ] DLQ / monitor alerts for `ALIGNMENT_*` error codes.
- [ ] Document consumer API in `docs/API.md`.

---

## 14. Documentation updates (on implementation)

| File | Change |
|------|--------|
| `AGENTS.md` | New alignment section, env vars, module location |
| `.env.example` | Alignment configuration block |
| `docs/CONFIGURATION.md` | Alignment settings reference |
| `docs/ARCHITECTURE.md` | Updated worker pipeline diagram |
| `docs/API.md` | Job result fields: `alignment_path`, JSON schema link |
| `IMPLEMENTATION_STATUS.md` | Track rollout status |

---

## 15. Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| TTS synthetic audio differs from natural speech | Drift, wrong word boundaries | Use forced align (known text); benchmark on real cloned voices; log `probability` |
| Mixed ZH/EN alignment quality | Missing or merged words | Script segmentation (Phase 2); flag `alignment_quality` in JSON |
| CPU alignment too slow for long audio | Job latency | `small` model; mandatory acceptance; future: sentence-level coarse pass |
| Extra torch dependency on macOS dev | Heavier local setup | Document in `MACOS_SETUP.md`; alignment uses CPU only |
| Alignment failure blocks delivery | Stricter than audio-only | Retries + circuit breaker; monitor error rate |

---

## 16. Open questions

1. **Backend contract:** Does the API gateway expect `alignment_path` in the RabbitMQ response, or will it poll S3 by convention (`{job_id}.json`)?
2. **Audio format:** Worker currently outputs WAV locally; S3 template may be `.mp3`. Alignment reads local WAV before any MP3 transcode — confirm upload pipeline does not transcode before alignment (align first, then transcode if needed).
3. **Word granularity for Chinese:** Whisper may align at character or sub-word level for ZH. Is character-level acceptable for subtitles?
4. **Failure policy confirmation:** Mandatory means job fails if alignment fails — confirm product accepts this vs. delivering audio without timestamps.

---

## 17. Summary

Add a **mandatory CPU-only `stable-whisper` small-model alignment step** to the worker **after time-stretching** and **before S3 upload**. Use script-based language selection for IndexTTS's ZH/EN content. Publish a JSON sidecar next to each audio file and return `alignment_path` in the job result. Keep alignment out of the synthesis cache and out of `infer.py` to preserve separation of concerns and correct timestamp semantics across `ratio` values.
