# Test Suite

This project uses pytest. Tests are split into two suites based on external-service requirements.

## Structure

```text
tests/
├── conftest.py          # sys.path setup + .env loader + shared fixtures (s3_client)
├── unit/                # Fast, hermetic, mocked (~2–3 s, no external services)
└── integration/         # Live external tests (~70 s, requires .env with real credentials)
```

## Running Tests

| Target | Command | Duration | Requirements |
|---|---|---|---|
| **Unit tests** | `uv run pytest tests/unit` | ~2–3 s | None (fully mocked) |
| **Integration tests** | `uv run pytest tests/integration` | ~70 s | Live S3, PostgreSQL, RabbitMQ |
| **All tests** | `uv run pytest` | ~75 s | All services running |

## Integration Test Prerequisites

Integration tests require a `.env` file in the project root with real credentials:

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/indextts_worker
RABBITMQ_URL=amqp://user:pass@host:5672/
S3_TTS_ACCESS_KEY_ID=...
S3_TTS_SECRET_ACCESS_KEY=...
S3_TTS_ENDPOINT_URL=...
# (and other S3_* vars as needed)
```

Tests that detect missing credentials skip themselves automatically — no manual
marker filtering required.

## Installation

```bash
# Install dev dependencies (includes pytest)
uv sync

# macOS-specific extras
uv pip install -e ".[mac]"
```

## Common Options

```bash
# Show print statements
uv run pytest tests/unit -s

# Run a single file
uv run pytest tests/unit/test_circuit_breaker.py -v

# Run with coverage
uv run pytest --cov=services
```
