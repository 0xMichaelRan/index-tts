Proceed with implementation with Suggested Phased Execution Roadmap

1. **Phase 1 (Core Alignment MVP):**
   * Add `stable-ts` to [pyproject.toml](file:///Users/aa/git/github_uncgra/indexTTS-worker/pyproject.toml) and test environment sync (`uv sync`).
   * Create `services/alignment.py` with `AlignmentService` singleton (CPU, Whisper `small`).
   * Integrate into `process_job()` in [services/tts_worker.py](file:///Users/aa/git/github_uncgra/indexTTS-worker/services/tts_worker.py) post time-stretch and pre-upload.
   * Add unit and mock integration tests in `tests/test_alignment.py` and `tests/pytest/test_tts_worker_alignment.py`.
2. **Phase 2 (Bilingual & Edge Cases):**
   * Implement script segmentation for mixed ZH/EN text.
   * Verify character vs. word token boundaries for Chinese subtitles.
3. **Phase 3 (Docs & Production Hardening):**
   * Update [AGENTS.md](file:///Users/aa/git/github_uncgra/indexTTS-worker/AGENTS.md), `.env.example`, and API documentation.
