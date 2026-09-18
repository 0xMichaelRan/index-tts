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
