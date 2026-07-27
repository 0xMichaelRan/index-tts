# S3 Integration Tests

These tests verify real S3 connectivity, authentication, and file operations using actual credentials from `.env`.

## Overview

The integration tests in `test_s3_integration.py` perform real S3 operations to verify:

- ✅ **Connectivity**: Can connect to both storage and output buckets
- ✅ **Authentication**: S3 credentials are valid and authorized
- ✅ **Upload**: Files can be uploaded to both buckets
- ✅ **Download**: Files can be downloaded from both buckets
- ✅ **File Operations**: List, delete, exists checks work correctly
- ✅ **Presigned URLs**: URLs can be generated with correct signatures
- ✅ **Error Handling**: Appropriate errors are raised for invalid operations

## Prerequisites

1. **S3 Credentials**: All `S3_STORAGE_*` and `S3_OUTPUT_*` variables must be set in `.env`
2. **S3 Buckets**: Buckets must exist and be accessible
3. **Permissions**: Credentials must have read/write permissions as needed
4. **Network**: Connection to S3 endpoints must be available

## Running the Tests

### All Integration Tests
```bash
set -a && source .env && set +a && uv run pytest tests/pytest/test_s3_integration.py -v -s
```

### Connectivity Tests Only (Fast)
```bash
set -a && source .env && set +a && uv run pytest tests/pytest/test_s3_integration.py::TestS3Connectivity -v -s
```

### Error Handling Tests
```bash
set -a && source .env && set +a && uv run pytest tests/pytest/test_s3_integration.py::TestS3ErrorHandling -v -s
```

### Upload/Download Tests (Slow)
```bash
set -a && source .env && set +a && uv run pytest tests/pytest/test_s3_integration.py -v -m slow
```

### Exclude Slow Tests
```bash
set -a && source .env && set +a && uv run pytest tests/pytest/test_s3_integration.py -v -m "not slow"
```

## Test Categories

### TestS3Connectivity (4 tests - ~9s)
Verifies that both buckets are accessible and credentials are valid.

- `test_storage_bucket_accessible` - List files in storage bucket
- `test_output_bucket_accessible` - List files in output bucket
- `test_storage_bucket_credentials_valid` - Generate presigned URL (validates credentials)
- `test_output_bucket_credentials_valid` - Generate presigned URL (validates credentials)

**Expected**: All pass ✅

### TestS3Upload (3 tests - ~5s, marked as `slow`)
Tests uploading files to both buckets with metadata.

- `test_upload_to_storage_bucket` - Upload test file and verify
- `test_upload_to_output_bucket` - Upload with job metadata
- `test_upload_with_metadata` - Upload with custom metadata

**Expected**: All pass ✅
**Cleanup**: Test files are automatically deleted after verification

### TestS3Download (2 tests - ~5s, marked as `slow`)
Tests downloading files from both buckets.

- `test_download_from_storage_bucket` - Upload then download from storage
- `test_download_from_output_bucket` - Upload then download from output

**Expected**: All pass ✅
**Cleanup**: Test files are automatically deleted

### TestS3FileOperations (4 tests - ~10s)
Tests file existence checks, listing, and deletion.

- `test_file_exists_check` - Check file existence before/after upload/delete
- `test_list_files_in_storage_bucket` - List files in storage bucket
- `test_list_files_in_output_bucket` - List files in output bucket
- `test_delete_file_from_storage` - Delete file from storage bucket

**Expected**: All pass ✅
**Cleanup**: Test files are automatically deleted

### TestS3PresignedURLs (2 tests - ~1s)
Tests presigned URL generation for both buckets.

- `test_presigned_url_storage_bucket` - Generate GET URL
- `test_presigned_url_output_bucket` - Generate PUT URL

**Expected**: All pass ✅
**No Side Effects**: No files created

### TestS3DualBucketSeparation (2 tests - ~1s)
Verifies dual-bucket separation.

- `test_storage_and_output_are_different` - Check bucket names
- `test_storage_and_output_use_different_clients` - Check separate clients

**Expected**: All pass ✅
**No Side Effects**: No files created

### TestS3ErrorHandling (3 tests - ~3s)
Tests proper error handling for invalid operations.

- `test_download_nonexistent_file` - Should raise S3ConfigError
- `test_upload_nonexistent_local_file` - Should raise FileNotFoundError
- `test_invalid_bucket_type` - Should raise ValueError

**Expected**: All pass ✅
**No Side Effects**: No files created

## Output Example

```
======================== test session starts ========================
platform darwin -- Python 3.10.19, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/aa/git/github_uncgra/indexTTS-worker
collected 20 items

tests/pytest/test_s3_integration.py::TestS3Connectivity::test_storage_bucket_accessible PASSED [ 5%]
tests/pytest/test_s3_integration.py::TestS3Connectivity::test_output_bucket_accessible PASSED [ 10%]
tests/pytest/test_s3_integration.py::TestS3Connectivity::test_storage_bucket_credentials_valid PASSED [ 15%]
tests/pytest/test_s3_integration.py::TestS3Connectivity::test_output_bucket_credentials_valid PASSED [ 20%]
tests/pytest/test_s3_integration.py::TestS3Upload::test_upload_to_storage_bucket PASSED [ 25%]
tests/pytest/test_s3_integration.py::TestS3Upload::test_upload_to_output_bucket PASSED [ 30%]
tests/pytest/test_s3_integration.py::TestS3Upload::test_upload_with_metadata PASSED [ 35%]
tests/pytest/test_s3_integration.py::TestS3Download::test_download_from_storage_bucket PASSED [ 40%]
tests/pytest/test_s3_integration.py::TestS3Download::test_download_from_output_bucket PASSED [ 45%]
tests/pytest/test_s3_integration.py::TestS3FileOperations::test_file_exists_check PASSED [ 50%]
tests/pytest/test_s3_integration.py::TestS3FileOperations::test_list_files_in_storage_bucket PASSED [ 55%]
tests/pytest/test_s3_integration.py::TestS3FileOperations::test_list_files_in_output_bucket PASSED [ 60%]
tests/pytest/test_s3_integration.py::TestS3FileOperations::test_delete_file_from_storage PASSED [ 65%]
tests/pytest/test_s3_integration.py::TestS3PresignedURLs::test_presigned_url_storage_bucket PASSED [ 70%]
tests/pytest/test_s3_integration.py::TestS3PresignedURLs::test_presigned_url_output_bucket PASSED [ 75%]
tests/pytest/test_s3_integration.py::TestS3DualBucketSeparation::test_storage_and_output_are_different PASSED [ 80%]
tests/pytest/test_s3_integration.py::TestS3DualBucketSeparation::test_storage_and_output_use_different_clients PASSED [ 85%]
tests/pytest/test_s3_integration.py::TestS3ErrorHandling::test_download_nonexistent_file PASSED [ 90%]
tests/pytest/test_s3_integration.py::TestS3ErrorHandling::test_upload_nonexistent_local_file PASSED [ 95%]
tests/pytest/test_s3_integration.py::TestS3ErrorHandling::test_invalid_bucket_type PASSED [100%]

======================== 20 passed in 45.32s ========================
```

## Important Notes

### Side Effects
- **Test Files**: All test files created during uploads are automatically cleaned up after verification
- **No Permanent Changes**: Tests are designed to not leave artifacts in S3
- **Idempotent**: Safe to run multiple times

### Environment Variables
- Tests are **skipped automatically** if S3 credentials are not configured
- Use `source .env && set +a` to load variables before running tests
- All variables must be set for tests to run

### Performance
- **Fast tests** (connectivity, presigned URLs, error handling): ~10s
- **Slow tests** (upload, download): ~20s
- **Total**: ~45s for all 20 tests

### Troubleshooting

#### Tests are skipped
```
SKIPPED S3 credentials not configured in .env
```
**Solution**: Load env variables before running tests
```bash
set -a && source .env && set +a
```

#### Connection timeout
```
S3ConfigError: Connection timeout
```
**Solution**: Check network connectivity and S3 endpoint URL is correct

#### Authentication failed
```
S3ConfigError: Access Denied
```
**Solution**: Verify credentials are correct and have required permissions

#### Bucket not found
```
S3ConfigError: NoSuchBucket
```
**Solution**: Verify bucket name exists and credentials have access

## Integration with CI/CD

### Skip Integration Tests (Default)
```bash
# Run only unit tests (mocked S3)
uv run pytest tests/ -v -m "not integration"
```

### Include Integration Tests
```bash
# Run all tests including integration
set -a && source .env && set +a && uv run pytest tests/ -v
```

### Pre-Deployment Check
```bash
# Before deploying, run connectivity tests
set -a && source .env && set +a && uv run pytest tests/pytest/test_s3_integration.py::TestS3Connectivity -v
```

## What's Tested

| Feature | Test | Status |
|---------|------|--------|
| Storage bucket connectivity | TestS3Connectivity | ✅ |
| Output bucket connectivity | TestS3Connectivity | ✅ |
| Storage credentials valid | TestS3Connectivity | ✅ |
| Output credentials valid | TestS3Connectivity | ✅ |
| Upload to storage | TestS3Upload | ✅ |
| Upload to output | TestS3Upload | ✅ |
| Upload with metadata | TestS3Upload | ✅ |
| Download from storage | TestS3Download | ✅ |
| Download from output | TestS3Download | ✅ |
| File exists in storage | TestS3FileOperations | ✅ |
| List files in storage | TestS3FileOperations | ✅ |
| List files in output | TestS3FileOperations | ✅ |
| Delete file from storage | TestS3FileOperations | ✅ |
| Presigned URL (storage) | TestS3PresignedURLs | ✅ |
| Presigned URL (output) | TestS3PresignedURLs | ✅ |
| Bucket separation | TestS3DualBucketSeparation | ✅ |
| Error on missing file | TestS3ErrorHandling | ✅ |
| Error on missing local file | TestS3ErrorHandling | ✅ |
| Error on invalid bucket type | TestS3ErrorHandling | ✅ |

## See Also

- `test_s3_config.py` - Unit tests with mocked S3
- `test_idempotent_upload.py` - Upload retry logic tests
- `DUAL_BUCKET_GUIDE.md` - Configuration guide
- `AGENTS.md` - Environment variable reference
