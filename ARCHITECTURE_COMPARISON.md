# Architecture Comparison: Before vs After

## File Structure

### ❌ BEFORE (Monolithic)

```
services/
├── tts_worker.py                   (~2000 lines)
│   ├── IndexTTSWorker.__init__()
│   │   ├── TTS engine init
│   │   ├── S3 client init
│   │   ├── Idempotent uploader init
│   │   ├── 3x Circuit breakers
│   │   ├── Cache manager init
│   │   └── Signal handlers
│   ├── IndexTTSWorker.connect_rabbitmq()
│   │   └── DLX queue setup
│   ├── IndexTTSWorker.process_job()
│   │   ├── Cache lookup
│   │   ├── Download audio
│   │   ├── Synthesis
│   │   ├── Cache store
│   │   ├── Time-stretching
│   │   ├── Alignment
│   │   ├── S3 upload
│   │   ├── Alignment upload
│   │   └── Cleanup
│   ├── IndexTTSWorker._synthesize_audio()
│   ├── IndexTTSWorker._download_audio_prompt()
│   ├── IndexTTSWorker._align_audio()
│   ├── IndexTTSWorker._upload_alignment()
│   ├── IndexTTSWorker._get_audio_duration()
│   ├── IndexTTSWorker._apply_time_stretch_to_file()
│   ├── IndexTTSWorker._apply_ratio_to_cached_audio()
│   ├── IndexTTSWorker.publish_result()
│   └── ... 30+ more methods
├── [other services...]
└── main (in tts_worker.py)
```

**Issues:**
- ❌ Single class with 30+ methods
- ❌ Mixed concerns (RabbitMQ, S3, audio, caching)
- ❌ Hard to test individual functionality
- ❌ Hard to reuse components
- ❌ Hard to modify without side effects
- ❌ 2000 lines in one file

---

### ✅ AFTER (Modular)

```
services/
├── __init__.py                          (Package exports)
├── tts_worker.py                        (~280 lines) - Main orchestrator
│   └── IndexTTSWorker
│       ├── __init__()                   (Component wiring)
│       ├── _init_tts_engine()
│       ├── _setup_signal_handlers()
│       └── start()                      (Main loop)
├── rabbitmq_manager.py                  (~280 lines) - Queue management
│   └── RabbitMQManager
│       ├── connect()
│       ├── disconnect()
│       ├── is_connected()
│       ├── reconnect_with_backoff()
│       ├── consume_messages()
│       ├── publish_result()
│       ├── acknowledge_message()
│       └── reject_message()
├── storage_manager.py                   (~160 lines) - S3 & file I/O
│   └── StorageManager
│       ├── download_audio_prompt()
│       ├── upload_audio()
│       ├── upload_alignment_json()
│       ├── cleanup_local_files()
│       ├── create_output_dir()
│       ├── create_cache_dir()
│       ├── get_temp_dir()
│       └── build_s3_output_path() (static)
├── audio_processor.py                   (~100 lines) - Audio operations
│   └── AudioProcessor
│       ├── get_audio_duration() (static)
│       ├── apply_time_stretch() (static)
│       ├── copy_audio_file() (static)
│       └── apply_ratio_to_audio() (static)
├── cache_manager.py                     (~150 lines) - Synthesis caching
│   └── CacheManager
│       ├── __init__()
│       ├── lookup()
│       └── store()
├── synthesis_pipeline.py                (~450 lines) - TTS workflow
│   └── SynthesisPipeline
│       ├── process_job()                (Main entry point)
│       ├── _run_synthesis()
│       ├── _synthesize_audio()
│       ├── _run_alignment()
│       ├── _extract_detected_language()
│       ├── _run_audio_upload()
│       ├── _run_alignment_upload()
│       ├── _build_success_result() (static)
│       └── _build_failure_result() (static)
├── [existing services...]
└── main (in tts_worker.py)
```

**Benefits:**
- ✅ 6 focused components with single responsibility
- ✅ Each component ~100-450 lines
- ✅ Independent testing possible
- ✅ Components reusable in other contexts
- ✅ Easy to modify without side effects
- ✅ Clear separation of concerns

---

## Component Responsibility Matrix

### Before (All Mixed in IndexTTSWorker)

| Concern | Lines | Mixed With |
|---------|-------|-----------|
| RabbitMQ | ~200 | Everything |
| S3 Operations | ~300 | Everything |
| Audio Processing | ~150 | Everything |
| Cache Operations | ~200 | Everything |
| TTS Synthesis | ~400 | Everything |
| Error Handling | ~250 | Everything |
| **TOTAL** | **~2000** | **All mixed** |

### After (Separated by Component)

| Component | Lines | Responsibility |
|-----------|-------|-----------------|
| RabbitMQManager | ~280 | Queue lifecycle only |
| StorageManager | ~160 | S3 + file I/O only |
| AudioProcessor | ~100 | Audio operations only |
| CacheManager | ~150 | Synthesis caching only |
| SynthesisPipeline | ~450 | TTS workflow orchestration |
| IndexTTSWorker | ~280 | Main orchestrator + queue loop |
| **TOTAL** | **~1420** | **Each has clear purpose** |

---

## Class Size Comparison

### Before
```
IndexTTSWorker
├── 35+ methods
├── 2000 lines
├── Mixed concerns
├── Multiple circuit breakers
├── Cache operations
├── S3 operations
├── RabbitMQ operations
├── Audio processing
└── Synthesis logic
```

### After

**IndexTTSWorker (280 lines, 5 methods)**
```
└── __init__()
    ├── TTS engine setup
    ├── StorageManager initialization
    ├── CacheManager initialization
    ├── SynthesisPipeline initialization
    ├── RabbitMQManager initialization
    └── Signal handler setup
└── _init_tts_engine()
└── _setup_signal_handlers()
└── start()
    └── Main RabbitMQ consumption loop
```

**RabbitMQManager (280 lines, 8 methods)**
```
└── Connection & queue management
```

**StorageManager (160 lines, 8 methods)**
```
└── S3 & file operations
```

**AudioProcessor (100 lines, 4 static methods)**
```
└── Audio processing
```

**CacheManager (150 lines, 2 methods)**
```
└── Synthesis caching
```

**SynthesisPipeline (450 lines, 8 methods)**
```
└── TTS workflow orchestration
```

---

## Method Distribution

### Before: All Methods in IndexTTSWorker

```python
class IndexTTSWorker:
    # Initialization (5 methods)
    def __init__()
    def _init_tts_engine()
    def _setup_signal_handlers()
    
    # RabbitMQ (8 methods)
    def connect_rabbitmq()
    def _is_connection_open()
    def _reconnect_with_backoff()
    def disconnect_rabbitmq()
    def publish_result()
    def _handle_publish_failure()
    def start()
    
    # Job Processing (1 method)
    def process_job()
    
    # Download (1 method)
    def _download_audio_prompt()
    
    # Synthesis (2 methods)
    def _init_tts_engine()
    def _synthesize_audio()
    
    # Audio Processing (3 methods)
    def _copy_cached_audio()
    def _apply_ratio_to_cached_audio()
    def _apply_time_stretch_to_file()
    
    # Cache (2 methods)
    def _process_cache_lookup()
    def _process_cache_store()
    
    # Alignment (2 methods)
    def _align_audio()
    def _upload_alignment()
    
    # Upload (1 method)
    def _upload_to_s3_idempotent()
    
    # Utilities (2 methods)
    def _get_audio_duration()
    def _cleanup_local_files()
    
    # Helper Functions (3 functions)
    def _build_s3_output_path()
    
    # TOTAL: 35+ methods
```

### After: Methods Distributed Across Components

```python
# RabbitMQManager (8 methods)
├── connect()
├── disconnect()
├── is_connected()
├── reconnect_with_backoff()
├── consume_messages()
├── publish_result()
├── acknowledge_message()
└── reject_message()

# StorageManager (8 methods)
├── download_audio_prompt()
├── upload_audio()
├── upload_alignment_json()
├── cleanup_local_files()
├── create_output_dir()
├── create_cache_dir()
├── get_temp_dir()
└── build_s3_output_path() [static]

# AudioProcessor (4 static methods)
├── get_audio_duration()
├── apply_time_stretch()
├── copy_audio_file()
└── apply_ratio_to_audio()

# CacheManager (2 methods)
├── lookup()
└── store()

# SynthesisPipeline (8 methods)
├── process_job()
├── _run_synthesis()
├── _synthesize_audio()
├── _run_alignment()
├── _extract_detected_language()
├── _run_audio_upload()
├── _run_alignment_upload()
├── _build_success_result() [static]
└── _build_failure_result() [static]

# IndexTTSWorker (5 methods)
├── __init__()
├── _init_tts_engine()
├── _setup_signal_handlers()
└── start()

# TOTAL: 39 methods (but organized logically)
```

---

## Dependency Graph

### Before
```
Everything depends on everything in IndexTTSWorker
(circular, implicit dependencies)
```

### After
```
          RabbitMQManager
                  ↑
                  │
          IndexTTSWorker ← [orchestrator]
                  │
    ┌─────────────┼─────────────┐
    │             │             │
    ▼             ▼             ▼
SynthesisPipeline StorageManager CacheManager
    │             │
    ├─→ AudioProcessor
    ├─→ AlignmentService
    └─→ CircuitBreakers

(Clear hierarchy, explicit dependencies)
```

---

## Testing Complexity

### Before
```
To test one concern (e.g., S3 upload):
1. Mock TTS engine
2. Mock RabbitMQ
3. Mock cache
4. Mock circuit breakers
5. Mock alignment service
6. ... (many mocks)
7. Finally test S3 logic

Result: Complex, brittle tests
```

### After
```
To test S3 upload:
1. import StorageManager
2. storage = StorageManager()
3. Test upload_audio()

Result: Simple, isolated tests
```

---

## Code Reusability

### Before
```python
# Can't reuse S3 operations without:
# - Creating entire IndexTTSWorker
# - Mocking everything else
# - Dealing with initialization complexity

from services.tts_worker import IndexTTSWorker
worker = IndexTTSWorker(...)  # Massive setup
```

### After
```python
# Easy to reuse individual components:

from services.storage_manager import StorageManager
storage = StorageManager()
path = storage.download_audio_prompt(job_id, s3_key)

from services.audio_processor import AudioProcessor
duration = AudioProcessor.get_audio_duration(audio_path)

from services.cache_manager import CacheManager
cache = CacheManager(cache_dir)
hit, cached_path = cache.lookup(job_id, text, prompt_path, ratio)
```

---

## Modification Scenarios

### Scenario 1: Add new caching backend (Redis)

**Before:**
- Modify IndexTTSWorker.process_job() (~10 places)
- Modify IndexTTSWorker._process_cache_lookup() (~50 lines)
- Modify IndexTTSWorker._process_cache_store() (~50 lines)
- Risk breaking RabbitMQ, S3, synthesis logic

**After:**
- Create RedisBackedCacheManager(CacheManager)
- Replace line: `self.cache_manager = RedisBackedCacheManager()`
- Done. Other components unaffected.

### Scenario 2: Add metric collection

**Before:**
- Add instrumentation throughout 2000-line file
- Risk breaking logic with metric code

**After:**
- Create MetricsMiddleware for each component
- Add one decorator per method
- Clean separation

### Scenario 3: Add retry logic to S3 uploads

**Before:**
- Find _upload_to_s3_idempotent() in 2000-line file
- Understand surrounding context
- Modify carefully

**After:**
- Open StorageManager.upload_audio()
- Modify retry logic in ~20 lines
- Clear, isolated change

---

## Summary Table

| Aspect | Before | After | Improvement |
|--------|--------|-------|-------------|
| **File Count** | 1 | 7 | +600% modularity |
| **Class Size** | 2000 lines | 280-450 lines | 78-86% reduction |
| **Methods per Class** | 35+ | 2-8 | Avg 5 per class |
| **Testability** | Hard | Easy | Isolated tests |
| **Reusability** | Not possible | Easy | Component reuse |
| **Maintainability** | Low | High | Clear concerns |
| **Documentation** | Implicit | Explicit | Self-documenting |
| **Bug Surface** | Large | Small | Reduced blast radius |
| **Learning Curve** | Steep | Shallow | Easier onboarding |

---

## Conclusion

The refactoring transforms the codebase from a large, difficult-to-understand monolith into a clean, modular architecture. Each component has a clear purpose, making the codebase easier to test, maintain, and extend.

**All functionality is preserved with significantly improved code quality.**
