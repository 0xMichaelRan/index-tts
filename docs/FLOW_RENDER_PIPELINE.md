# Flow Video Render Pipeline Documentation

**Status**: ✅ Fully Implemented  
**Modules**: `services/flow_render_pipeline.py`, `services/flow_render_consumer.py`, `services/synthesis_pipeline.py`, `services/worker_config.py`  
**Worker Integration**: `services/tts_worker.py` (Linux/Windows daemon thread)  
**Version**: 1.0  
**Last Updated**: 2026-09-08  

---

## Overview

The **Flow Render Pipeline** enables the IndexTTS Worker to process `flow` jobs for multi-locale narrated video generation. A Flow job takes:
1. **Input text**: Three paragraphs in 3 locales (`en`, `zh-CN`, `zh-TW`).
2. **Video clips**: 10 user-uploaded video clips stored in S3.

The worker performs two distinct phases:
- **Phase 1: TTS Synthesis & Forced Alignment**: Synthesizes speech and extracts word-level alignment timestamps for all 3 locales sequentially via the standard `tts_jobs` queue.
- **Phase 2: Video Composition & FFmpeg Rendering**: Consumes from the `flow_render_jobs` queue, segments audio into 10 proportional windows, speed-adjusts each clip to match speech duration using FFmpeg (`setpts` + chained `atempo`), overlays the full narration track, and uploads 3 final `.mp4` videos to S3.

> [!IMPORTANT]
> Flow jobs are handled **entirely by the IndexTTS Worker**. The Remotion video worker is not involved.

---

## Architecture & Message Flow

```
┌─────────────────┐
│ studio-backend  │
└────────┬────────┘
         │ 1. Publishes 3 sequential TTS messages (jobType="flow", locale="en"|"zh-CN"|"zh-TW")
         ▼
┌──────────────────────────────────────────────┐
│ RabbitMQ: tts_jobs queue                     │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│ IndexTTS Worker (Synthesis Pipeline)         │
│  - Synthesizes audio (.mp3)                  │
│  - Generates word-level alignment (.json)    │
│  - Caches files in outputs/tts_output/       │
│  - Uploads to S3: flow/YYYYMMDD/{id}/{loc}.* │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│ RabbitMQ: tts_results queue                  │
│ (Echoes jobId, jobType="flow", locale)       │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│ FlowOrchestrator│
│ (studio-backend)│
└────────┬────────┘
         │ 2. When all 3 locales finish TTS, publishes to flow_render_jobs
         ▼
┌──────────────────────────────────────────────┐
│ RabbitMQ: flow_render_jobs queue             │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│ IndexTTS Worker (FlowRenderConsumer thread)  │
│  - Checks local outputs/tts_output first     │
│  - Falls back to S3 download if uncached     │
│  - Downloads 10 video clips to temp dir      │
│  - For each locale (en, zh-CN, zh-TW):       │
│      * Proportional audio window segmentation│
│      * FFmpeg speed adjustment per clip      │
│      * Concat clips + audio overlay          │
│  - Uploads 3 MP4s: flow/YYYYMMDD/{id}/*.mp4  │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│ RabbitMQ: flow_render_results queue          │
└──────────────────────────────────────────────┘
```

---

## Component Details

### 1. Synthesis Pipeline (`services/synthesis_pipeline.py`)

- **Job Type Validation**: Accepts `job_type == "flow"` alongside standard `"studio"` and `"rem"` jobs.
- **Locale Handling**: Reads the `locale` field (`en`, `zh-CN`, `zh-TW`) from incoming payloads.
- **Output Naming**: Audio and alignment JSON are saved under `flow/{YYYYMMDD}/{job_id}/{locale}.mp3` and `.json`.
- **Result Schema**: Both success and error result dictionaries echo the `locale` field so `FlowOrchestrator` can track each locale's state independently.

### 2. Flow Render Pipeline (`services/flow_render_pipeline.py`)

The pipeline orchestrates the multi-locale rendering process:

#### A. Cache Resolution
Before requesting audio or alignment files from S3, the pipeline checks the local synthesis directory (`outputs/tts_output/`). If the worker that synthesised the audio is the same worker performing the render, network download overhead is eliminated entirely.

#### B. Clip-to-Audio Segmentation Algorithm
Each of the 10 video clips corresponds to a slice of synthesized audio:
1. Total character count across all words in the locale alignment is summed.
2. Target characters per clip is `target_chars = total_chars / 10`.
3. Alignment timestamps are traversed to find 10 proportional time boundaries `(start_k, end_k)`.
4. If alignment timestamps have gaps or anomalies, fallback uniform segmentation is applied across the total audio duration.

#### C. FFmpeg Speed Adjustment
For each clip `k`:
- `clip_natural_duration` is probed via `ffprobe`.
- `window_duration_k = end_k - start_k`.
- Target speed factor:
  $$\text{speed\_factor} = \frac{\text{clip\_natural\_duration}}{\text{window\_duration\_k}}$$
  Clamped to range `[0.25, 4.0]`.
- Video stream adjustment: `-filter:v "setpts=PTS/{speed_factor}"`.
- Audio stream adjustment: Chained `atempo` filters. Because FFmpeg's `atempo` filter strictly requires parameters in `[0.5, 2.0]`, values outside this range are factored into multiple filters:
  - Example `speed=0.25`: `atempo=0.5,atempo=0.5`
  - Example `speed=3.0`: `atempo=2.0,atempo=1.5`

#### D. Concat & Final Assembly
- The 10 adjusted clips are concatenated using the FFmpeg concat demuxer.
- The original full synthesized narration audio replaces the concatenated video's audio.
- Video resolution (`720p`, `1080p`, etc.) and aspect ratio (`16x9`, `9x16`, etc.) are applied with `-c:v libx264 -pix_fmt yuv420p` for broad player compatibility.
- Temporary files and scratch directories are safely purged in a `finally` block.

### 3. Flow Render Consumer (`services/flow_render_consumer.py`)

- **Thread Architecture**: Runs in a background daemon thread (`FlowRenderConsumer`) managed by `IndexTTSWorker`.
- **Dedicated AMQP Connection**: Maintains its own blocking Pika connection separate from the TTS channel to prevent connection lock contention.
- **Prefetch QoS**: Configured with `prefetch_count=1` to ensure only one resource-intensive rendering job is processed at a time.
- **Queue Declaration**:
  - Active declaration for `flow_render_jobs` with durable exchange `flow_render_jobs.dlx` and DLQ `flow_render_jobs_failed`.
  - Active declaration for `flow_render_results` with durable exchange `flow_render_results.dlx` and DLQ `flow_render_results_failed`.
- **Publish Resilience**: Exponential backoff retry (up to 3 attempts) when publishing to `flow_render_results`.

---

## Configuration

Add the following environment variables to your `.env` file (see `.env.example`):

```bash
# ============================
# Flow Render Consumer
# (Linux/Windows only — ignored on macOS)
# ============================

# Enable the flow_render_jobs consumer (default: true)
FLOW_RENDER_ENABLED=true

# Number of parallel locale render threads per flow job (default: 3)
FLOW_RENDER_WORKERS=3

# Path to ffmpeg binary (must be on PATH or absolute path)
FLOW_RENDER_FFMPEG_PATH=ffmpeg
```

### OS Platform Behavior

| Platform | Consumer Status | Reasoning |
|---|---|---|
| **Linux (NVIDIA GPU)** | Active (daemon thread) | Primary production render environment |
| **Windows (NVIDIA GPU)** | Active (daemon thread) | Supported for GPU workstations |
| **macOS (Darwin)** | Disabled | Apple Silicon/macOS builds run TTS only; no GPU video rendering |

---

## Message Specifications

### 1. `flow_render_jobs` Payload (Input)

```json
{
  "jobId": 123,
  "jobType": "flow",
  "audioEnPath": "flow/20260908/123/en.mp3",
  "audioZhCnPath": "flow/20260908/123/zh-CN.mp3",
  "audioZhTwPath": "flow/20260908/123/zh-TW.mp3",
  "alignEnPath": "flow/20260908/123/en.json",
  "alignZhCnPath": "flow/20260908/123/zh-CN.json",
  "alignZhTwPath": "flow/20260908/123/zh-TW.json",
  "clipS3Keys": [
    "flow-clips/42/clip_01.mp4",
    "flow-clips/42/clip_02.mp4",
    "flow-clips/42/clip_03.mp4",
    "flow-clips/42/clip_04.mp4",
    "flow-clips/42/clip_05.mp4",
    "flow-clips/42/clip_06.mp4",
    "flow-clips/42/clip_07.mp4",
    "flow-clips/42/clip_08.mp4",
    "flow-clips/42/clip_09.mp4",
    "flow-clips/42/clip_10.mp4"
  ],
  "resolution": "720p",
  "ratioFormat": "16x9",
  "createdAt": "2026-09-08T05:00:00Z"
}
```

### 2. `flow_render_results` Payload (Success)

```json
{
  "jobId": "123",
  "jobType": "flow",
  "status": "completed",
  "videoEnPath": "flow/20260908/123/en.mp4",
  "videoZhCnPath": "flow/20260908/123/zh-CN.mp4",
  "videoZhTwPath": "flow/20260908/123/zh-TW.mp4",
  "renderDurationSeconds": 38.4,
  "completedAt": "2026-09-08T05:01:20Z"
}
```

### 3. `flow_render_results` Payload (Failure)

```json
{
  "jobId": "123",
  "jobType": "flow",
  "status": "failed",
  "errorCode": "subprocess.CalledProcessError",
  "errorMessage": "Command '['ffmpeg', ...]' returned non-zero exit status 1.",
  "renderDurationSeconds": 12.1,
  "completedAt": "2026-09-08T05:00:35Z"
}
```

---

## Dead Letter Exchange & Retry Policies

- **Input Queue (`flow_render_jobs`)**:
  - Critical queue.
  - Poison pills (invalid JSON or missing fields) are acknowledged with `requeue=False`, moving the payload to the Dead Letter Queue (`flow_render_jobs_failed`).
  - Worker shutdowns during rendering reject with `requeue=True` to preserve the job for another worker or restart.
- **Output Queue (`flow_render_results`)**:
  - Bound to DLX `flow_render_results.dlx` routing to `flow_render_results_failed`.
  - Message TTL: 7 days (`604800000 ms`).
  - Max queue length: 5,000 items.

---

## Verification & Troubleshooting

### Diagnostics
Check the worker startup logs to verify the consumer state:
```text
Flow render consumer: ENABLED (ffmpeg: ffmpeg)
FlowRenderConsumer: connected to RabbitMQ, listening on 'flow_render_jobs'
FlowRenderConsumer thread started
```

### Common Issues
1. **FFmpeg missing or outdated**: Ensure `ffmpeg` and `ffprobe` (version 4.4+) are installed and accessible on system `PATH`, or configure `FLOW_RENDER_FFMPEG_PATH`.
2. **Missing clip keys on S3**: Verify that clips were uploaded to the Video bucket prior to triggering the flow job.
3. **macOS execution**: `FlowRenderConsumer` is automatically disabled on macOS (`Darwin`). Do not attempt to run flow render jobs on a Mac worker.
