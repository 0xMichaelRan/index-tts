# Pytest Cheatsheet for IndexTTS Tests

Quick reference for common pytest commands using `uv run pytest`.

## Basic Commands

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run with output capture disabled (see print statements)
uv run pytest -s

# Run with both verbose and output (recommended for debugging)
uv run pytest -v -s

# Run specific test file
uv run pytest test_platform.py
uv run pytest test_macos_tts.py

# Run specific test class
uv run pytest test_platform.py::TestImports
uv run pytest test_macos_tts.py::TestMacOSTTS

# Run specific test function
uv run pytest test_platform.py::TestImports::test_core_imports
uv run pytest test_macos_tts.py::test_platform_check
```

## Filtering & Selection

```bash
# Run tests matching a pattern
uv run pytest -k "import"

# Run tests matching multiple patterns
uv run pytest -k "test_imports or test_factory"

# Skip tests matching a pattern
uv run pytest -k "not gpu"

# Run only tests that failed last time
uv run pytest --lf

# Run failed tests first, then others
uv run pytest --ff

# Stop after first failure
uv run pytest -x

# Stop after N failures
uv run pytest --maxfail=2
```

## Output & Reporting

```bash
# Show extra test summary info
uv run pytest -ra

# Show local variables on failure
uv run pytest -l

# Generate HTML report
uv run pytest --html=report.html

# Show test durations (top 10)
uv run pytest --durations=10

# Quiet output (minimal)
uv run pytest -q

# Extra verbose (shows setup/teardown)
uv run pytest -vv
```

## Platform-Specific Testing

```bash
# Run only macOS tests (automatically skipped on other platforms)
uv run pytest test_macos_tts.py -v -s

# Run only platform detection tests
uv run pytest test_platform.py::test_platform_info -v -s

# Run tests that would run on GPU (skips on macOS)
uv run pytest test_platform.py::TestGPUInference -v -s

# Run everything except GPU tests
uv run pytest -k "not gpu"
```

## Debugging

```bash
# Drop into Python debugger on failure
uv run pytest --pdb

# Drop into debugger at start of each test
uv run pytest --trace

# Show full diff on assertion failures
uv run pytest -vv

# Show local variables in tracebacks
uv run pytest -l

# Extra detailed output
uv run pytest -vv --tb=long
```

## Markers

```bash
# Run tests marked as 'macos'
uv run pytest -m macos

# Run tests NOT marked as 'cuda'
uv run pytest -m "not cuda"

# Run tests marked as either 'macos' or 'cuda'
uv run pytest -m "macos or cuda"
```

## Test Collection

```bash
# Collect tests without running them
uv run pytest --collect-only

# Show test collection with full paths
uv run pytest --collect-only -q

# Show which test file each test comes from
uv run pytest --collect-only -q test_*.py
```

## Advanced

```bash
# Run tests in random order (useful for finding hidden dependencies)
uv run pytest --random-order

# Run tests in parallel (requires pytest-xdist)
# uv run pytest -n auto

# Generate coverage report (requires pytest-cov)
# uv run pytest --cov=indextts --cov-report=html

# Watch for file changes and re-run tests (requires pytest-watch)
# uv run ptw
```

## Common Workflows

### Quick sanity check
```bash
uv run pytest test_platform.py::test_platform_info -v -s
```

### Run all available tests on current platform
```bash
uv run pytest -v -s
```

### Debug a failing test
```bash
uv run pytest test_file.py::TestClass::test_method -vv -s -l
```

### Run and see timings
```bash
uv run pytest -v --durations=10
```

### Generate a report
```bash
uv run pytest -v --tb=short > test_report.txt
uv run pytest --html=report.html --self-contained-html
```

## Useful Environment Variables

```bash
# Run tests with Python warnings displayed
PYTHONWARNINGS=all uv run pytest

# Increase pytest verbosity (internal)
PYTEST_CURRENT_TEST=1 uv run pytest -v

# Python optimization (disable asserts!)
# PYTHONOPTIMIZE=1 uv run pytest  # NOT RECOMMENDED for tests
```

## Exit Codes

- `0`: All tests passed
- `1`: Tests failed
- `2`: Test execution was interrupted
- `3`: Internal error
- `4`: Command line usage error
- `5`: No tests collected

## Tips

1. **Use `-v -s` together** for best debugging experience
2. **Use `-x` to stop on first failure** when debugging
3. **Use `-k pattern` to quickly filter tests** during development
4. **Run `--collect-only` first** to verify what tests will run
5. **Use `--tb=short`** in CI, `--tb=long` when debugging
6. **Create fixtures in `conftest.py`** to share setup code
