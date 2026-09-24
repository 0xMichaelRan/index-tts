#!/usr/bin/env python
"""
Unified REST API Runner for IndexTTS & Vox Media Pipelines.
Usage:
    uv run python api.py [--host 0.0.0.0] [--port 8848] [--reload]
"""

from __future__ import annotations

import argparse
import uvicorn


def main():
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
