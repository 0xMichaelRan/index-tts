#!/usr/bin/env python
"""
Unified REST API Runner for IndexTTS & Vox Media Pipelines.

Launches a FastAPI application serving REST endpoints for text-to-speech (TTS)
synthesis and Vox video rendering pipelines. Designed for local development,
interactive testing, and HTTP-driven integration.

Usage:
    uv run python api.py [--host 0.0.0.0] [--port 8848] [--reload]

Options:
    --host      Host IP address to bind the server (default: 0.0.0.0).
    --port      Port number to bind the server (default: 8848).
    --reload    Enable auto-reload for development on code changes.

Interactive Documentation:
    Swagger UI:  http://<host>:<port>/docs
    ReDoc:       http://<host>:<port>/redoc

API Endpoints:
    GET  /
        Root metadata and route discovery (lists active platform and endpoints).
    GET  /health
        Health check verifying server status, active engine, and S3 connectivity.
    POST /api/v1/tts/synthesize
        Full production TTS pipeline. Accepts JSON matching the RabbitMQ `tts_jobs`
        wire protocol (camelCase). Performs text sanitization, cache lookup,
        inference, LUFS normalization, mandatory forced alignment, and S3 upload.
    POST /api/v1/tts/direct
        Direct TTS inference returning audio/wav binary stream directly in the response.
        Accepts multipart/form-data (`text`, `audio_prompt`, `speed_ratio`).
        Ideal for quick local listening tests without S3 uploads or alignment.
    POST /api/v1/vox/render
        Full Vox video render pipeline. Accepts JSON matching the RabbitMQ `vox_jobs`
        wire protocol (camelCase). Downloads assets, aligns clips to narration beats,
        renders MP4 via FFmpeg, and uploads to S3.
    POST /infer/
        Legacy direct inference endpoint (deprecated; redirects behavior to /api/v1/tts/direct).

Platform Behavior:
    - macOS (Darwin): Uses native AVFoundation speech synthesis (no GPU required).
    - Linux / Windows: Uses IndexTTS GPU deep learning inference (CUDA required;
      audio_prompt file must be provided for voice cloning).

Examples:
    Health check:
        curl http://localhost:8848/health

    Direct TTS synthesis (macOS):
        curl -X POST http://localhost:8848/api/v1/tts/direct \\
            -F "text=Hello, this is a test." \\
            -o output.wav

    Direct TTS synthesis with voice cloning (Linux/GPU):
        curl -X POST http://localhost:8848/api/v1/tts/direct \\
            -F "text=Hello, this is a test." \\
            -F "audio_prompt=@/path/to/voice_sample.wav" \\
            -o output.wav

    Full pipeline synthesis:
        curl -X POST http://localhost:8848/api/v1/tts/synthesize \\
            -H "Content-Type: application/json" \\
            -d '{"jobId": "test-001", "jobType": "tts", "text": "Hello world", "audioPromptPath": "prompts/voice.wav"}'
"""

from __future__ import annotations

import argparse
import uvicorn


def main():
    """Parse command-line arguments and run the Uvicorn ASGI server."""
    parser = argparse.ArgumentParser(
        description="Run IndexTTS Multi-Pipeline Worker API"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host address to bind server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8848,
        help="Port to bind server (default: 8848)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    args = parser.parse_args()

    uvicorn.run(
        "services.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
