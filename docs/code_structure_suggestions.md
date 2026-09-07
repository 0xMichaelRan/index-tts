# Code Structure Improvement Suggestions

## Summary

The codebase is already well-structured — `tts_worker.py` is a clean orchestrator, and responsibilities are clearly separated across `services/`. These suggestions focus on **readability**, **maintainability**, and **reducing friction** when you work daily in this file.

---

## 1. Extract `WorkerConfig` dataclass (High Value)

**Problem**: `tts_worker.py.__init__` reads 8+ env vars inline, mixed with initialization logic. Adding a new config option means digging into the constructor.

```diff
# NEW: services/worker_config.py
@dataclass
class WorkerConfig:
    rabbitmq_url: str
    use_fast_inference: bool = True
    normalization_enabled: bool = True
    normalization_target_lufs: float = -16.0
    cache_enabled: bool = True
    cache_max_entries: int = 10_000
    cache_eviction_threshold: int = 9_000
    cache_dir: str = "outputs/tts_cache"
    log_level: str = "INFO"
    log_file_enabled: bool = False
    log_file_path: str = "logs/worker.log"

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        """Load all config from environment variables."""
        ...
```

`IndexTTSWorker.__init__` becomes:
```python
def __init__(self, config: WorkerConfig):
    self.config = config
    ...
```

**Benefit**: One place to see all knobs. Easy to override in tests without patching `os.environ`.

---

## 2. Extract `_extract_job_id()` helper (Low effort, high frequency)

**Problem**: This pattern appears **3 times** across `tts_worker.py` and `synthesis_pipeline.py`:
```python
job_id = job_data.get("jobId") if job_data.get("jobId") is not None else job_data.get("job_id")
```

**Fix**: One-liner in a shared utility:
```python
# services/job_utils.py
def extract_job_id(job_data: dict) -> str | None:
    """Resolve jobId from camelCase or snake_case key."""
    val = job_data.get("jobId") ?? job_data.get("job_id")
    return str(val) if val is not None else None
```

---

## 3. Deduplicate S3 circuit breaker instantiation (Medium)

**Problem**: `_run_synthesis`, `_run_audio_upload`, and `_run_alignment_upload` in `synthesis_pipeline.py` each call:
```python
s3_breaker = get_circuit_breaker("S3Download", failure_threshold=5, reset_timeout=60)
```
inline — importing `get_circuit_breaker` inside the method body. This is re-looked-up on every job.

**Fix**: Initialize `self.s3_breaker` once in `SynthesisPipeline.__init__`, just like `self.tts_breaker` and `self.alignment_breaker` are already done.

```diff
- # In _run_synthesis, _run_audio_upload, _run_alignment_upload:
- from services.circuit_breaker import get_circuit_breaker
- s3_breaker = get_circuit_breaker("S3Download", ...)
- with s3_breaker: ...

+ # In __init__:
+ self.s3_breaker = get_circuit_breaker("S3Download", failure_threshold=5, reset_timeout=60)

+ # In methods:
+ with self.s3_breaker: ...
```

---

## 4. Promote `message_callback` to a named method (Medium)

**Problem**: `message_callback` is defined as a closure inside `IndexTTSWorker.start()`. It's 60+ lines long and directly references `self.*` through closure capture. This makes it:
- Invisible to grep/IDE navigation
- Untestable in isolation (no way to call it from a test)
- Hard to read `start()` at a glance

**Fix**: Extract to `IndexTTSWorker._handle_message(ch, method, properties, body)` and pass it:
```python
def start(self):
    ...
    self.rabbitmq_manager.consume_messages(
        callback=self._handle_message,
        prefetch_count=1,
    )

def _handle_message(self, ch, method, properties, body):
    """Handle a single incoming RabbitMQ job message."""
    ...
```

---

## 5. Replace inline `max_retries=3` with config constant (Low effort)

**Problem**: `max_retries = 3` is hardcoded in `synthesis_pipeline.py:171` with no env var hook. It's also unrelated to the RabbitMQ-level `MAX_RETRY_COUNT` env var mentioned in docs.

**Fix**: Read from env (consistent with other config) or promote to `WorkerConfig`:
```python
self.max_retries = int(os.getenv("TTS_PIPELINE_MAX_RETRIES", "3"))
```

---

## 6. `TTSJobService._run_coroutine` → move to `app/` or a shared util (Low)

**Problem**: `tts_job_service.py` contains a general-purpose async-to-sync bridge (`_run_coroutine`). This couples database concerns with an unrelated async utility. It's also fragile — spawns a new `threading.Thread` per DB call.

**Alternative**: Use `asyncio.run()` if there's no running event loop, or maintain a single persistent event loop thread for DB ops. At minimum, move `_run_coroutine` to `app/database.py` or a `services/async_utils.py`.

---

## 7. Cleanup logic: prefer `contextlib.ExitStack` over nested `finally` checks (Optional)

**Problem**: `synthesis_pipeline.process_job`'s `finally` block manually checks each path:
```python
if local_audio_prompt and os.path.exists(local_audio_prompt): ...
if local_output and not local_output.startswith(...): ...
if local_alignment_json and os.path.exists(local_alignment_json): ...
```

This scatters cleanup logic and is easy to miss when adding a new temp file.

**Fix**: Collect paths to clean into a list during processing; clean the list in `finally`:
```python
_cleanup_paths: list[str] = []
...
# When acquiring a temp file:
local_audio_prompt = self.storage_manager.download_audio_prompt(...)
_cleanup_paths.append(local_audio_prompt)
...
finally:
    for p in _cleanup_paths:
        if p and os.path.exists(p):
            self.storage_manager.cleanup_local_files(p)
```

---

## Priority Summary

| # | Suggestion | Files Affected | Effort | Impact |
|---|------------|----------------|--------|--------|
| 1 | `WorkerConfig` dataclass | `tts_worker.py` + new file | Medium | High |
| 2 | `extract_job_id()` helper | `tts_worker.py`, `synthesis_pipeline.py` | Low | Medium |
| 3 | Deduplicate S3 circuit breaker | `synthesis_pipeline.py` | Low | Medium |
| 4 | `_handle_message` method | `tts_worker.py` | Low | High |
| 5 | `max_retries` from env | `synthesis_pipeline.py` | Low | Low |
| 6 | Move `_run_coroutine` | `tts_job_service.py` | Low | Low |
| 7 | ExitStack cleanup | `synthesis_pipeline.py` | Medium | Medium |
