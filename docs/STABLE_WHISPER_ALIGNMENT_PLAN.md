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
│    output: {job_id}_raw_alignment.json  ← kept locally  │
│            {job_id}_alignment.srt       ← kept locally  │
│            {job_id}_alignment.json      ← parsed JSON   │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Step 4: Upload audio + parsed alignment JSON to S3      │
│         (SRT and raw Whisper JSON stay on local disk)   │
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

### Model configuration (verified API)

```python
import stable_whisper

model = stable_whisper.load_model("small", device="cpu")

# Forced alignment call (keyword args required)
result = model.align(
    audio=audio_path,
    text=text,
    language=language_hint,
)
```

> **Note:** `cpu_preload` is **not** a valid parameter — `stable_whisper.load_model` proxies `whisper.load_model(name, device, download_root, in_memory)`. Verified against `stable-ts>=2.19.1`.

| Setting | Value | Rationale |
|---------|-------|-----------|
| Model | `small` | Balance of accuracy vs CPU latency (~500 MB weights) |
| Device | `cpu` | Avoid GPU contention with IndexTTS; worker is GPU-saturated during synthesis |
| Load timing | Worker `__init__` (singleton) | Amortize load cost across jobs; ~2–5 s one-time startup |
| FP16 | Off on CPU | Use default float32 on CPU |

### Dependency addition (`pyproject.toml`)

Add to main dependencies (alignment is mandatory, not optional):

```toml
"stable-ts",
"torch",
"torchaudio",
```

`stable-ts` transitively brings in `openai-whisper`. `torch` and `torchaudio` are listed explicitly so macOS CPU-only dev machines resolve them without pulling CUDA extras. Do not pin versions here — let `uv` resolve compatible versions at sync time against the existing `torch` pin in the `cuda` extras.

> **macOS note:** On Apple Silicon, force CPU — do **not** use `device="mps"`. MPS has float64 tensor conversion crashes during alignment (verified). The worker already sets `device="cpu"` unconditionally.

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

#### §5.1 v1: monolingual-first; two-pass deferred to Phase 2

Whisper align does not reveal segment boundaries before alignment runs, making exact audio-slice attribution non-trivial. For v1 the **simpler fallback table below is the definitive v1 contract**. The two-pass (Pass A + Pass B) approach is reserved for Phase 2 when segment-level accuracy data is available.

#### v1 alignment strategy (definitive)

| Text profile | v1 strategy | `alignment_quality` value |
|--------------|-------------|---------------------------|
| `language=zh`, no Latin letters | `model.align(audio=path, text=text, language="zh")` | `"monolingual_zh"` |
| `language=en`, no CJK | `model.align(audio=path, text=text, language="en")` | `"monolingual_en"` |
| Mixed ZH/EN | `model.align(audio=path, text=text, language="zh")` — majority-script wins | `"mixed_fallback"` |

For mixed text, majority script is determined by character count of CJK vs Latin runs in the input. If Latin characters exceed 40% of total alphanumeric chars, use `language="en"` instead. Log `alignment_quality: "mixed_fallback"` in the JSON output so consumers can apply QA filtering.

Per-segment slicing (Phase 2) will improve mixed-text accuracy but is not required for v1.

---

## 6. `ratio` parameter: synthesis handling and alignment impact

### How `ratio` flows through the synthesis pipeline

The `ratio` job parameter controls playback speed. Critically, **IndexTTS itself ignores `ratio`** — the engine always synthesises at its natural pace. Speed adjustment is applied by the worker as a post-synthesis time-stretch step using `librosa.effects.time_stretch`.

```
process_job(ratio=R)
  ├── TTS synthesis   → base_audio_path  (always ratio=1.0, cached)
  ├── time-stretch    → local_output     (only if R != 1.0; librosa time_stretch(rate=R))
  └── forced align    → on local_output  (the final delivered waveform)
```

Key behaviour in [`_synthesize_audio()`](file:///Users/aa/git/github_uncgra/indexTTS-worker/services/tts_worker.py#L840):
- `ratio` is always passed as `1.0` to `infer()` / `infer_fast()` — the IndexTTS model never sees the real ratio.
- `_apply_ratio_to_cached_audio()` (or `_apply_time_stretch_to_file()`) performs the actual time-stretch on a **copy** of the base audio, producing `local_output`.

### How `ratio` affects forced alignment

| Scenario | `local_output` used for alignment | Effect on timestamps |
|----------|-----------------------------------|----------------------|
| `ratio=1.0` (no stretch) | Base audio copy | Timestamps at natural TTS speed |
| `ratio=1.5` (faster) | Time-stretched copy (~2/3 duration) | All timestamps proportionally compressed |
| `ratio=0.8` (slower) | Time-stretched copy (~1.25× duration) | All timestamps proportionally expanded |

**Critical rule (unchanged from §3):** Alignment **must** run on `local_output` — i.e., the final time-stretched local WAV — never on `base_audio_path`. The synthesis cache stores only `base_audio_path`; alignment is never cached and always computed fresh per job.

**Rationale:** Timestamps are valid only for a specific waveform duration. If alignment ran on the base `ratio=1.0` audio and the consumer received the `ratio=1.5` audio, every word timestamp would be off by a factor of 1.5. Aligning the final delivered file guarantees timestamps match playback.

### Interaction with synthesis cache

| Scenario | Audio aligned | Cache stores alignment? |
|----------|---------------|-------------------------|
| Cache miss, `ratio=1.0` | `base_audio_path` (copied) | **No** |
| Cache miss, `ratio=1.5` | Time-stretched copy of base | **No** |
| Cache hit, `ratio=1.0` | Copied base audio | **No** |
| Cache hit, `ratio=0.8` | Time-stretched copy of base | **No** |

Alignment is always computed fresh per job. Expected CPU cost: ~1–4 s per minute of audio on `small`/CPU (benchmark during implementation).

---

## 7. Output format

### Local files

Three alignment files are generated locally after alignment:

```
outputs/tts_output/{job_id}/{job_id}_raw_alignment.json  ← native stable-whisper output; kept on disk; NOT uploaded
outputs/tts_output/{job_id}/{job_id}_alignment.srt       ← SRT subtitle; kept on disk; NOT uploaded
outputs/tts_output/{job_id}/{job_id}_alignment.json      ← parsed/distilled JSON; uploaded to S3, then deleted
```

**Rationale for separating raw and parsed JSON:**
- The native stable-whisper JSON contains verbose internal fields (token probabilities, internal Whisper segments, mel offsets) that are not needed by downstream video rendering (Remotion).
- The **parsed JSON** (`{job_id}_alignment.json`) is a distilled representation — only the fields required for Remotion video rendering (see schema in §7.3 below) — making it compact and stable for API consumers.
- The **raw JSON** (`{job_id}_raw_alignment.json`) is retained on local disk for debugging and re-parsing without re-running alignment.
- The **SRT** (`{job_id}_alignment.srt`) is retained on local disk for downstream subtitle transcoding without re-running alignment.

**Temp file cleanup policy:**
- `{job_id}_raw_alignment.json` — **retained** on local disk; never uploaded.
- `{job_id}_alignment.srt` — **retained** on local disk; never uploaded.
- `{job_id}_alignment.json` (parsed) — uploaded to S3, then **deleted** from local disk (same lifecycle as `local_output`).

> **Note:** `stable-ts` emits SRT/VTT natively via `result.to_srt_vtt()` and raw JSON via `result.to_dict()`. All three files are produced from the same `AlignmentResult` object in a single alignment pass — no extra Whisper inference is needed.

### S3 paths

Only the parsed alignment JSON is uploaded. Mirror the audio path using stem substitution:

```
Audio:          tts-audio/studio/{job_id}.mp3
Alignment JSON: tts-audio/studio/{job_id}.json   ← only this is uploaded
```

Derive S3 path from `output_s3_path`:

```python
base_s3 = output_s3_path.rsplit(".", 1)[0]
alignment_s3_path = base_s3 + ".json"
# subtitle_s3_path is NOT derived — SRT stays on local disk only
```

### JSON schema (v1)

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

**`alignment_quality` values:** `"monolingual_zh"` | `"monolingual_en"` | `"mixed_fallback"` (see §5 v1 strategy table).

- **`segments`:** Per script/language block (length 1 for monolingual jobs).
- **`words`:** Flattened global timeline for easy subtitle consumption.
- **`probability`:** Whisper token confidence; useful for QA filtering.

### Worker result payload extension

Add to `process_job()` success dict sent back via RabbitMQ:

```python
{
    ...
    "alignment_path": "tts-audio/studio/{job_id}.json",  # S3 key of the parsed JSON
    "alignment_duration_seconds": 1.87,
}
```

> **Note:** `subtitle_path` is **not** included in the RabbitMQ response. The SRT file lives only on local worker disk. The API gateway and downstream consumers (e.g. Remotion) access alignment data exclusively via `alignment_path` (the parsed JSON in S3).

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
3. **`process_job()`:** After `local_output` is finalized (post time-stretch), call alignment; upload **only the parsed JSON** to S3; extend result with `alignment_path`. Do **not** upload SRT or raw JSON.
4. **New helpers:**
   - `_align_audio(job_id, local_output, text, language) -> tuple[str, str, str]` → `(local_raw_json_path, local_srt_path, local_parsed_json_path)`
   - `_upload_alignment(job_id, local_parsed_json, json_s3_path) -> str`

**Temp file registration in `finally` block:**
```python
# Parsed alignment JSON is uploaded then removed (same as local_output)
if local_alignment_json:
    self._cleanup_local_files(local_alignment_json)
# Raw Whisper JSON and SRT are intentionally NOT cleaned up — kept on local disk
# local_raw_alignment_json  → retained
# local_alignment_srt       → retained
```

### Changes to `services/idempotent_upload.py`

Reuse `IdempotentUploader.upload_with_retry()` for the parsed JSON sidecar upload (same integrity semantics as audio). No upload is made for the SRT or raw Whisper JSON.

---

## 9. Configuration

Add to `.env.example`:

```bash
# ============================
# Forced Alignment (stable-whisper)
# ============================

# Model name (default: small)
TTS_ALIGNMENT_MODEL=small

# Device for alignment (must be cpu — GPU reserved for TTS; do NOT use mps on Apple Silicon)
TTS_ALIGNMENT_DEVICE=cpu

# Directory where Whisper model weights are cached (default: ~/.cache/whisper)
# Set to a persistent path in containerised deployments
TTS_ALIGNMENT_MODEL_DIR=/opt/models/whisper

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
| Empty / whitespace-only text | `ALIGNMENT_INVALID_INPUT` — caught in `process_job()` **before** alignment is called; job fails immediately | No |
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
- Verify after job completion: parsed `{job_id}_alignment.json` is deleted locally (uploaded to S3); raw `{job_id}_raw_alignment.json` and `{job_id}_alignment.srt` are retained on local disk.

### Worker tests (`tests/pytest/test_tts_worker_alignment.py`)

- Mock `AlignmentService.align()` — verify called with `local_output` after stretch.
- Mock failure — job returns `ALIGNMENT_FAILED`.
- Result dict includes `alignment_path` (parsed JSON S3 key) but NOT `subtitle_path`.
- Verify only parsed JSON is uploaded; SRT and raw JSON are not passed to `IdempotentUploader`.

### Manual QA checklist

- [ ] Monolingual Chinese studio job → valid ZH word timestamps.
- [ ] Monolingual English playground job → valid EN word timestamps.
- [ ] Mixed ZH/EN job → words cover full text (no missing Latin or CJK).
- [ ] `ratio=1.5` job → timestamps match slowed/sped audio in a video editor.
- [ ] Cache hit + `ratio != 1.0` → alignment on stretched `local_output`, not cached base audio.
- [ ] Worker restart → model reloads on CPU without CUDA OOM.
- [ ] After job completes: parsed `{job_id}_alignment.json` deleted (uploaded to S3); raw `{job_id}_raw_alignment.json` retained; `{job_id}_alignment.srt` retained on local disk.
- [ ] Digit/symbol text (e.g. `$50`, `2026年`) — verify SRT word boundaries are sensible (see §16 Text Normalization note).

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
| Mixed ZH/EN alignment quality | Missing or merged words | Script segmentation (Phase 2); flag `alignment_quality: "mixed_fallback"` in JSON |
| CPU alignment too slow for long audio | Job latency | `small` model; mandatory acceptance; future: sentence-level coarse pass |
| MPS (Apple Silicon) float64 crash | Worker crash on macOS dev | Always use `device="cpu"`; never `"mps"` — verified crash on stable-ts |
| Whisper model not cached in container | Re-download on every restart | Set `TTS_ALIGNMENT_MODEL_DIR` to a persistent volume mount |
| Extra torch dependency on macOS dev | Heavier local setup | Document in `MACOS_SETUP.md`; alignment uses CPU only |
| Alignment failure blocks delivery | Stricter than audio-only | Retries + circuit breaker; monitor error rate |

---

## 16. Special notes

### Text Normalization vs Forced Alignment

IndexTTS passes raw job `text` through an internal `TextNormalizer` before BPE tokenisation. This means the **phonetic output may diverge from the raw input text** passed to `model.align()`:

- Numbers: `100` may be spoken as `一百` (ZH) or `one hundred` (EN).
- Currency: `$50` → `fifty dollars`.
- Dates: `2026年9月` → `二零二六年九月`.
- Abbreviations and mixed symbols are similarly expanded.

**Consequence for forced alignment:** `model.align(text=raw_text, ...)` presents Whisper with the original text, while the synthesised audio contains the normalised phonetic form. When there is significant divergence — particularly for digit-heavy, symbol-heavy, or currency-containing text — Whisper's token matching may produce incorrect or missing word timestamps.

**v1 stance (best-effort):** Pass raw `text` to `model.align()`. Log `alignment_quality: "mixed_fallback"` (or a new value `"normalization_mismatch_risk"`) for jobs containing digits, `$`, `%`, or other expandable tokens so downstream consumers can apply QA filtering.

**Future improvement (Phase 3):** Expose the normalised text from `TextNormalizer` and pass it to `model.align()` instead of raw text. This eliminates phoneme drift at the cost of coupling the alignment step to IndexTTS internals.

### Chinese Word Granularity

Whisper's ZH tokeniser produces timestamps at **character or sub-word level**, not at dictionary-word level. For a sentence like `你好世界`, timestamps may appear as `你`, `好`, `世界` or even `你好`, `世`, `界` depending on the Whisper model's subword segmentation. Character-level granularity is acceptable for most karaoke and subtitle use cases. If word-level segmentation is required (e.g. `jieba` words), a post-processing step merging character timestamps into dictionary words can be added in Phase 3.

---

## 17. Open questions

> **Resolved:** Audio format (always local WAV), failure policy (mandatory — alignment failure = job failure), and backend contract (`alignment_path` included in RabbitMQ response; `subtitle_path` not included) are all resolved and reflected in this plan.

No open questions remain. Please flag any new requirements or edge cases.

---

## 18. Summary

Add a **mandatory CPU-only `stable-whisper` small-model alignment step** to the worker **after time-stretching** and **before S3 upload**. Because `ratio` is handled entirely by the worker's post-synthesis time-stretch (not by IndexTTS itself), alignment always runs on `local_output` — the final delivered WAV — ensuring timestamps match the uploaded audio exactly.

**Three local files** are produced per job:
- `{job_id}_raw_alignment.json` — native stable-whisper output (kept on local disk, **not** uploaded)
- `{job_id}_alignment.srt` — SRT subtitle (kept on local disk, **not** uploaded)
- `{job_id}_alignment.json` — parsed/distilled JSON for Remotion video rendering (uploaded to S3, then deleted locally)

Only the **parsed JSON** is uploaded to S3. Return `alignment_path` (the S3 key of the parsed JSON) in the RabbitMQ job result; `subtitle_path` is not included. Keep alignment out of the synthesis cache and out of `infer.py` to preserve separation of concerns and correct timestamp semantics across `ratio` values.

Text normalisation divergence (digits, currency, abbreviations) is a known v1 limitation; raw text is passed to `model.align()` and flagged via `alignment_quality` metadata for downstream QA filtering.
