# Running Tests

This project uses pytest to manage tests. The test suite includes platform-specific tests that automatically skip when running on unsupported platforms.

## Installation

First, make sure pytest is installed:

```bash
# Install dev dependencies (includes pytest)
pip install -e ".[dev]"

# Or install just pytest
pip install pytest
```

For macOS-specific tests, also install:
```bash
pip install -e ".[mac]"
```

## Running Tests

### Run all tests
```bash
pytest
```

### Run specific test files
```bash
# Run only platform tests
pytest tests/test_platform.py -v

# Run only macOS TTS tests
pytest tests/test_macos_tts.py -v
```

### Run with output (see print statements)
```bash
pytest -s
```

### Run with verbose output
```bash
pytest -v
```

### Run with both verbose and captured output
```bash
pytest -v -s
```

## Test Organization

### test_platform.py
Platform-specific integration tests located in `tests/`:
- `TestImports`: Verifies core module imports
- `TestMacOSTTSPlatform`: macOS-specific TTS tests (auto-skipped on non-macOS)
- `TestFactory`: Tests the TTS factory function
- `TestGPUInference`: GPU/CUDA tests (auto-skipped on macOS)

### test_macos_tts.py
Dedicated macOS TTS functionality tests located in `tests/`:
- `TestMacOSTTS`: Complete test suite for macOS native TTS
  - Engine creation
  - Voice listing
  - Speech synthesis to audio
  - File output generation

All macOS tests automatically skip when running on non-macOS platforms.

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

```
Platform: Darwin 23.x.x
Python: 3.10.x

test_platform.py::TestImports::test_core_imports PASSED
test_platform.py::TestMacOSTTSPlatform::test_macos_tts_creation PASSED
test_platform.py::TestMacOSTTSPlatform::test_list_voices_macos PASSED
test_platform.py::TestMacOSTTSPlatform::test_synthesis_to_audio_macos PASSED
test_platform.py::TestFactory::test_factory_function_exists PASSED
test_platform.py::TestFactory::test_factory_creates_engine PASSED
test_platform.py::TestGPUInference::test_cuda_availability SKIPPED (CUDA not available on macOS)

test_macos_tts.py::TestMacOSTTS::test_engine_creation PASSED
test_macos_tts.py::TestMacOSTTS::test_list_voices PASSED
test_macos_tts.py::TestMacOSTTS::test_speech_synthesis_to_audio PASSED
test_macos_tts.py::TestMacOSTTS::test_file_output PASSED
```

## Troubleshooting

### "No module named 'AVFoundation'"
Install macOS dependencies:
```bash
pip install -e ".[mac]"
```

### "No module named 'torch'"
Install CUDA dependencies (Windows/Linux):
```bash
pip install -e ".[cuda]"
```

### Tests are skipped
This is expected behavior when platform-specific dependencies are not available. Tests will gracefully skip with informative messages.
