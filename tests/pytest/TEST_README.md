# Running Tests

This project uses pytest to manage tests. The test suite includes platform-specific tests that automatically skip when running on unsupported platforms.

## Test Infrastructure

- **Location**: All pytest tests are in `tests/pytest/`
- **Python path setup**: `tests/conftest.py` automatically adds the project root to `sys.path` so imports work correctly
- **Configuration**: `pytest.ini` at the project root configures pytest discovery and behavior
- **Package manager**: Tests should be run with `uv run pytest` for consistency with the project setup

## Installation

First, make sure pytest is installed:

```bash
# Install dev dependencies (includes pytest)
uv pip install -e ".[dev]"

# Or install just pytest
uv pip install pytest
```

For macOS-specific tests, also install:
```bash
uv pip install -e ".[mac]"
```

For S3 and RabbitMQ tests, boto3 and pika are already in the dependencies.

## Running Tests

### Run all tests
```bash
uv run pytest tests/pytest/ -v
```

### Run specific test files
```bash
# Run platform tests
uv run pytest tests/pytest/test_platform.py -v

# Run macOS TTS tests
uv run pytest tests/pytest/test_macos_tts.py -v

# Run RabbitMQ config tests
uv run pytest tests/pytest/test_rabbitmq_config.py -v

# Run RabbitMQ connection tests
uv run pytest tests/pytest/test_rabbitmq_worker_connection.py -v

# Run S3 config tests
uv run pytest tests/pytest/test_s3_config.py -v
```

### Run with output (see print statements)
```bash
uv run pytest tests/pytest/ -s
```

### Run with verbose output
```bash
uv run pytest tests/pytest/ -v
```

### Run with both verbose and captured output
```bash
uv run pytest tests/pytest/ -v -s
```

## Test Organization

All pytest tests are located in `tests/pytest/`:

### test_platform.py
Platform-specific integration tests:
- `TestImports`: Verifies core module imports
- `TestMacOSTTSPlatform`: macOS-specific TTS tests (auto-skipped on non-macOS)
- `TestFactory`: Tests the TTS factory function
- `TestGPUInference`: GPU/CUDA tests (auto-skipped on macOS)

### test_macos_tts.py
Dedicated macOS TTS functionality tests:
- `TestMacOSTTS`: Complete test suite for macOS native TTS
  - Engine creation
  - Voice listing
  - Speech synthesis to audio
  - File output generation

### test_rabbitmq_config.py
RabbitMQ configuration tests:
- `TestRabbitMQURLParsing`: URL parsing and validation
- `TestQueueConfiguration`: Queue configuration and routing
- `TestConnectionRetry`: Connection retry logic and backoff
- `TestConfigureQueue`: Single queue configuration
- `TestConfigureQueues`: Full queue configuration workflow
- `TestGetQueueInfo`: Queue information retrieval
- `TestIntegration`: Integration tests with real RabbitMQ

### test_rabbitmq_worker_connection.py
RabbitMQ worker connection tests:
- `TestRabbitMQWorkerConnection`: Connection and queue availability tests
  - Basic connection verification
  - TTS jobs queue availability
  - TTS results queue availability
  - Connection URL parsing

### test_s3_config.py
S3 storage configuration tests:
- `TestS3ClientInitialization`: S3Client initialization and validation
- `TestPathValidation`: Path validation and security
- `TestFileOperations`: File upload, download, delete operations
- `TestPresignedURLs`: Presigned URL generation
- `TestListFiles`: File listing functionality
- `TestContentTypeDetection`: Content type detection
- `TestPathStructure`: Path structure constants
- `TestErrorHandling`: Error handling
- `TestUploadAudio`: Audio-specific upload functionality

## Platform-Specific Behavior

### On macOS
- All tests run except GPU inference tests
- Requires `pip install -e ".[mac]"` for full functionality
- Will skip macOS tests gracefully if AVFoundation dependencies not installed

### On Windows/Linux
- All tests run except macOS-specific tests
- Requires `pip install -e ".[cuda]"` for GPU tests
- Will skip GPU tests if PyTorch/CUDA not available

## Test Markers

Tests are automatically marked based on platform requirements:
- macOS-specific tests use `@pytest.mark.skipif(platform.system() != "Darwin")`
- GPU tests use `@pytest.mark.skipif(platform.system() == "Darwin")`

## Expected Output

Running `uv run pytest tests/pytest/ -v` should show output similar to:

```
Platform: Darwin 23.x.x
Python: 3.10.x

test_macos_tts.py::TestMacOSTTS::test_engine_creation PASSED
test_macos_tts.py::TestMacOSTTS::test_list_voices PASSED
test_macos_tts.py::TestMacOSTTS::test_speech_synthesis_to_audio PASSED
test_macos_tts.py::TestMacOSTTS::test_file_output PASSED
test_macos_tts.py::test_platform_check PASSED

test_platform.py::TestImports::test_core_imports PASSED
test_platform.py::TestMacOSTTSPlatform::test_macos_tts_creation PASSED
test_platform.py::TestMacOSTTSPlatform::test_list_voices_macos PASSED
test_platform.py::TestMacOSTTSPlatform::test_synthesis_to_audio_macos PASSED
test_platform.py::TestFactory::test_factory_function_exists PASSED
test_platform.py::TestFactory::test_factory_creates_engine PASSED
test_platform.py::TestGPUInference::test_cuda_availability SKIPPED (CUDA not available on macOS)
test_platform.py::test_platform_info PASSED

test_rabbitmq_config.py::TestRabbitMQURLParsing::test_parse_basic_url PASSED
test_rabbitmq_config.py::TestRabbitMQURLParsing::test_parse_url_with_vhost PASSED
test_rabbitmq_config.py::TestRabbitMQURLParsing::test_parse_url_with_defaults PASSED
test_rabbitmq_config.py::TestRabbitMQURLParsing::test_parse_invalid_url FAILED
... (23 tests total in rabbitmq_config.py)

test_rabbitmq_worker_connection.py::TestRabbitMQWorkerConnection::test_rabbitmq_connection PASSED
test_rabbitmq_worker_connection.py::TestRabbitMQWorkerConnection::test_tts_jobs_queue_exists PASSED
test_rabbitmq_worker_connection.py::TestRabbitMQWorkerConnection::test_tts_results_queue_exists PASSED
test_rabbitmq_worker_connection.py::TestRabbitMQWorkerConnection::test_connection_details_parsed_correctly PASSED

test_s3_config.py::TestS3ClientInitialization::test_init_with_environment_variables PASSED
test_s3_config.py::TestS3ClientInitialization::test_init_with_explicit_parameters PASSED
test_s3_config.py::TestS3ClientInitialization::test_init_missing_required_configuration PASSED
... (52 tests total in s3_config.py)

========================= 75 tests collected in 0.16s ==============
```

**Total: 75 pytest tests organized across 5 test modules**

## Troubleshooting

### "No module named 'AVFoundation'"
Install macOS dependencies:
```bash
pip install -e ".[mac]"
# or with uv:
uv pip install -e ".[mac]"
```

### "No module named 'torch'"
Install CUDA dependencies (Windows/Linux):
```bash
pip install -e ".[cuda]"
# or with uv:
uv pip install -e ".[cuda]"
```

### "ModuleNotFoundError: No module named 'services'"
This error is fixed by the `conftest.py` file at `tests/conftest.py`, which adds the project root to the Python path. If you see this error:
1. Ensure you're running pytest from the project root: `uv run pytest tests/pytest/`
2. Check that `tests/conftest.py` exists
3. Restart your pytest runner if running in watch mode

### RabbitMQ Connection Tests Timeout
The RabbitMQ connection tests require a running RabbitMQ instance. If tests timeout:
1. Ensure RabbitMQ is running and accessible
2. Set the `RABBITMQ_URL` environment variable in `.env`
3. Check that the URL is correct (default: `amqp://guest:guest@localhost:5672/`)

### S3 Configuration Tests Fail
The S3 tests use mocking by default. If they fail:
1. Ensure boto3 is installed: `pip install boto3`
2. For integration tests, set S3 environment variables in `.env`:
   - `S3_ENDPOINT_URL`
   - `S3_ACCESS_KEY_ID`
   - `S3_SECRET_ACCESS_KEY`
   - `S3_BUCKET_NAME`
   - `S3_REGION`

### Tests Are Skipped
This is expected behavior when platform-specific dependencies are not available. Tests will gracefully skip with informative messages. Check the output for `SKIPPED` markers.
