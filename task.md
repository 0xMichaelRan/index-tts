# Tasks — S3 TOML Registry Migration (Clean Break)

- [x] Create `config/buckets.toml`
- [/] Create `services/s3_registry.py` (registry loader, S3BucketConfig, helpers)
- [ ] Rewrite `services/s3_config.py` (S3Client uses registry, no old dual-bucket code)
- [ ] Rewrite `services/idempotent_upload.py` (remove create_uploader factory, update bucket_type strings)
- [ ] Update `services/storage_manager.py` (use "misc"/"audio" bucket types)
- [ ] Update `services/vox_render_pipeline.py` (use "misc"/"video"/"audio" bucket types)
- [ ] Update `services/logging_config.py` (log_startup_summary signature update)
- [ ] Update `services/tts_worker.py` (startup logs from registry)
- [ ] Update `.env` (remove old S3_MISC_*, R2_VOICE_*; add S3_MISC/VIDEO/AUDIO creds only)
- [ ] Update `.env.example`
- [ ] Add `tests/test_s3_registry.py`
- [ ] Update `tests/pytest/test_idempotent_upload.py` (fix mock_s3_client, remove old vars)
- [ ] Run tests to verify
