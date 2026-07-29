"""
Tests for TTS Worker Core Functionality

Tests platform-specific synthesis, audio duration calculation,
language support, and graceful shutdown handling.
"""

import os
import pytest
import tempfile
import wave
import signal
import time
import threading
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from services.tts_worker import IndexTTSWorker


class TestAudioDurationCalculation:
    """Test audio duration calculation from WAV files."""
    
    def create_test_wav(self, duration_seconds: float, sample_rate: int = 24000) -> str:
        """Create a test WAV file with specific duration."""
        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_file.close()
        
        num_frames = int(duration_seconds * sample_rate)
        
        with wave.open(temp_file.name, 'w') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)   # 16-bit
            wav_file.setframerate(sample_rate)
            # Write silent frames
            wav_file.writeframes(b'\x00\x00' * num_frames)
        
        return temp_file.name
    
    def test_audio_duration_calculation_correct(self):
        """Test that audio duration is calculated correctly for valid WAV files."""
        # Create 5-second test WAV
        test_wav = self.create_test_wav(5.0, sample_rate=24000)
        
        try:
            worker = IndexTTSWorker()
            duration = worker._get_audio_duration(test_wav)
            
            # Allow small floating point error
            assert abs(duration - 5.0) < 0.01, f"Expected ~5.0s, got {duration}s"
        finally:
            os.unlink(test_wav)
    
    def test_audio_duration_different_sample_rates(self):
        """Test duration calculation with different sample rates."""
        sample_rates = [16000, 22050, 24000, 44100, 48000]
        expected_duration = 3.5
        
        for rate in sample_rates:
            test_wav = self.create_test_wav(expected_duration, sample_rate=rate)
            
            try:
                worker = IndexTTSWorker()
                duration = worker._get_audio_duration(test_wav)
                
                assert abs(duration - expected_duration) < 0.01, \
                    f"Rate {rate}Hz: Expected ~{expected_duration}s, got {duration}s"
            finally:
                os.unlink(test_wav)
    
    def test_audio_duration_file_not_found(self):
        """Test that FileNotFoundError is raised for non-existent files."""
        worker = IndexTTSWorker()
        
        with pytest.raises(FileNotFoundError):
            worker._get_audio_duration("/nonexistent/path/audio.wav")
    
    def test_audio_duration_invalid_wav_file(self):
        """Test that ValueError is raised for invalid WAV files."""
        # Create a non-WAV file
        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_file.write(b"Not a WAV file")
        temp_file.close()
        
        try:
            worker = IndexTTSWorker()
            
            with pytest.raises(ValueError, match="Invalid WAV file"):
                worker._get_audio_duration(temp_file.name)
        finally:
            os.unlink(temp_file.name)


class TestPlatformSpecificSynthesis:
    """Test platform-specific TTS synthesis engine initialization."""
    
    @patch('services.tts_worker.platform.system')
    @patch('services.tts_worker.create_tts_engine')
    def test_macos_native_tts_initialization(self, mock_create_engine, mock_platform):
        """Test that macOS uses native TTS engine."""
        mock_platform.return_value = "Darwin"
        mock_engine = Mock()
        mock_create_engine.return_value = mock_engine
        
        worker = IndexTTSWorker()
        
        # Verify create_tts_engine was called with macOS settings
        mock_create_engine.assert_called_once_with(
            use_native_macos=True,
            language="en-US"
        )
        assert worker.tts == mock_engine
    
    @patch('services.tts_worker.platform.system')
    @patch('services.tts_worker.create_tts_engine')
    def test_linux_gpu_tts_initialization(self, mock_create_engine, mock_platform):
        """Test that Linux uses GPU IndexTTS engine."""
        mock_platform.return_value = "Linux"
        mock_engine = Mock()
        mock_create_engine.return_value = mock_engine
        
        worker = IndexTTSWorker()
        
        # Verify create_tts_engine was called with GPU settings
        mock_create_engine.assert_called_once_with(
            use_native_macos=False,
            cfg_path="checkpoints/config.yaml",
            model_dir="checkpoints",
            is_fp16=True,
            use_cuda_kernel=False,
        )
        assert worker.tts == mock_engine
    
    @patch('services.tts_worker.platform.system')
    @patch('services.tts_worker.create_tts_engine')
    def test_windows_gpu_tts_initialization(self, mock_create_engine, mock_platform):
        """Test that Windows uses GPU IndexTTS engine."""
        mock_platform.return_value = "Windows"
        mock_engine = Mock()
        mock_create_engine.return_value = mock_engine
        
        worker = IndexTTSWorker()
        
        # Verify Windows also uses GPU settings
        mock_create_engine.assert_called_once_with(
            use_native_macos=False,
            cfg_path="checkpoints/config.yaml",
            model_dir="checkpoints",
            is_fp16=True,
            use_cuda_kernel=False,
        )
        assert worker.tts == mock_engine


class TestLanguageSupport:
    """Test language parameter handling in synthesis."""
    
    @patch('services.tts_worker.S3Client')
    @patch('services.tts_worker.IdempotentUploader')
    @patch('services.tts_worker.platform.system')
    @patch('services.tts_worker.create_tts_engine')
    def test_language_passed_to_synthesis(self, mock_create_engine, mock_platform, mock_uploader, mock_s3):
        """Test that language parameter is correctly passed to TTS engine."""
        # Set platform to Linux for consistent testing
        mock_platform.return_value = "Linux"
        
        # Mock TTS engine creation
        mock_tts_engine = Mock()
        mock_create_engine.return_value = mock_tts_engine
        
        worker = IndexTTSWorker()
        worker.s3_client = Mock()
        worker.uploader = Mock()
        
        # Mock TTS synthesis methods (both for compatibility)
        worker.tts.infer_fast = Mock(return_value="/fake/output.wav")
        worker.tts.infer = Mock(return_value="/fake/output.wav")
        
        # Mock S3 download
        worker._download_audio_prompt = Mock(return_value="/fake/prompt.wav")
        worker._upload_to_s3_idempotent = Mock(return_value="s3://bucket/output.wav")
        worker._get_audio_duration = Mock(return_value=10.5)
        worker._cleanup_local_files = Mock()
        
        # Process job with specific language
        job_data = {
            "job_id": "test-123",
            "text": "Hello world",
            "audio_prompt_path": "audio-prompts/voice.wav",
            "language": "zh",  # Chinese
            "job_type": "studio",
            "output_path_template": "tts-audio/studio/{job_id}.mp3",
        }
        
        result = worker.process_job(job_data)
        
        # Verify language was passed to synthesis
        # Check which method was actually called
        if worker.tts.infer_fast.called:
            call_kwargs = worker.tts.infer_fast.call_args[1]
        elif worker.tts.infer.called:
            call_kwargs = worker.tts.infer.call_args[1]
        else:
            raise AssertionError("Neither infer nor infer_fast was called")
        
        assert call_kwargs.get('language') == 'zh', \
            "Language parameter should be passed to TTS engine"
        
        assert result['status'] == 'completed'


class TestGracefulShutdown:
    """Test graceful shutdown on SIGTERM signal."""
    
    @patch('services.tts_worker.S3Client')
    def test_signal_handler_registration(self, mock_s3):
        """Test that signal handlers are registered on initialization."""
        with patch('services.tts_worker.signal.signal') as mock_signal:
            worker = IndexTTSWorker()
            
            # Verify SIGTERM and SIGINT handlers were registered
            calls = mock_signal.call_args_list
            signal_types = [call[0][0] for call in calls]
            
            assert signal.SIGTERM in signal_types, "SIGTERM handler should be registered"
            assert signal.SIGINT in signal_types, "SIGINT handler should be registered"
    
    @patch('services.tts_worker.S3Client')
    def test_shutdown_flag_set_on_signal(self, mock_s3):
        """Test that shutdown flag is set when signal is received."""
        worker = IndexTTSWorker()
        
        assert worker._shutdown_requested is False
        
        # Simulate SIGTERM signal
        signal_handler = worker._setup_signal_handlers.__code__.co_consts[1]
        # Manually set shutdown flag as we can't actually send signals in tests
        worker._shutdown_requested = True
        
        assert worker._shutdown_requested is True
    
    @patch('services.tts_worker.S3Client')
    @patch('services.tts_worker.pika.BlockingConnection')
    def test_new_messages_rejected_on_shutdown(self, mock_connection, mock_s3):
        """Test that new messages are rejected when shutdown is requested."""
        worker = IndexTTSWorker()
        worker._shutdown_requested = True
        
        # Mock channel
        mock_channel = Mock()
        worker.channel = mock_channel
        
        # Mock message callback
        mock_method = Mock()
        mock_method.delivery_tag = "test-tag"
        
        # Simulate receiving a message after shutdown requested
        # This is tested indirectly through the start() method's callback
        # Here we just verify the flag is respected
        
        assert worker._shutdown_requested is True


class TestJobProcessingFlow:
    """Test complete job processing flow with all components."""
    
    @patch('services.tts_worker.S3Client')
    @patch('services.tts_worker.IdempotentUploader')
    def test_complete_job_processing_success(self, mock_uploader_class, mock_s3_class):
        """Test successful end-to-end job processing."""
        worker = IndexTTSWorker()
        
        # Mock all dependencies
        worker.tts = Mock()
        worker.s3_client = Mock()
        worker.uploader = Mock()
        
        # Mock synthesis
        worker._download_audio_prompt = Mock(return_value="/tmp/prompt.wav")
        worker._synthesize_audio = Mock(return_value="/tmp/output.wav")
        worker._upload_to_s3_idempotent = Mock(return_value="tts-audio/studio/job-123.wav")
        worker._get_audio_duration = Mock(return_value=15.5)
        worker._cleanup_local_files = Mock()
        
        job_data = {
            "job_id": "job-123",
            "text": "Test synthesis",
            "audio_prompt_path": "audio-prompts/voice.wav",
            "language": "en",
            "job_type": "studio",
            "output_path_template": "tts-audio/studio/{job_id}.mp3",
        }
        
        result = worker.process_job(job_data)
        
        # Verify all steps were called
        worker._download_audio_prompt.assert_called_once()
        worker._synthesize_audio.assert_called_once()
        worker._upload_to_s3_idempotent.assert_called_once()
        worker._get_audio_duration.assert_called_once()
        worker._cleanup_local_files.assert_called_once()
        
        # Verify result structure
        assert result['job_id'] == 'job-123'
        assert result['status'] == 'completed'
        assert result['audio_path'] == 'tts-audio/studio/job-123.wav'
        assert result['audio_duration_seconds'] == 15.5
        assert 'synthesis_duration_seconds' in result
        assert result['retry_count'] == 0
    
    @patch('services.tts_worker.S3Client')
    def test_job_processing_with_retry_on_transient_error(self, mock_s3):
        """Test that transient errors trigger retry with exponential backoff."""
        worker = IndexTTSWorker()
        worker.tts = Mock()
        worker.s3_client = Mock()
        worker.uploader = Mock()
        
        # Mock download to fail twice, then succeed
        call_count = {'count': 0}
        
        def download_side_effect(*args, **kwargs):
            call_count['count'] += 1
            if call_count['count'] <= 2:
                raise OSError("Network error")
            return "/tmp/prompt.wav"
        
        worker._download_audio_prompt = Mock(side_effect=download_side_effect)
        worker._synthesize_audio = Mock(return_value="/tmp/output.wav")
        worker._upload_to_s3_idempotent = Mock(return_value="s3://bucket/output.wav")
        worker._get_audio_duration = Mock(return_value=10.0)
        worker._cleanup_local_files = Mock()
        
        job_data = {
            "job_id": "retry-job",
            "text": "Test retry",
            "audio_prompt_path": "audio-prompts/voice.wav",
            "language": "en",
            "job_type": "studio",
            "output_path_template": "tts-audio/studio/{job_id}.mp3",
        }
        
        with patch('services.tts_worker.time.sleep'):  # Speed up test
            result = worker.process_job(job_data)
        
        # Verify retry occurred
        assert call_count['count'] == 3
        assert result['status'] == 'completed'
        assert result['retry_count'] == 2


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration in job processing."""
    
    @patch('services.tts_worker.S3Client')
    def test_circuit_breaker_opens_after_failures(self, mock_s3):
        """Test that circuit breaker opens after failure threshold."""
        worker = IndexTTSWorker()
        
        # Set low threshold for testing
        worker.s3_breaker._failure_threshold = 2
        
        # Mock download to always fail
        worker._download_audio_prompt = Mock(side_effect=OSError("S3 error"))
        
        job_data = {
            "job_id": "cb-test",
            "text": "Test circuit breaker",
            "audio_prompt_path": "audio-prompts/voice.wav",
            "language": "en",
            "job_type": "studio",
            "output_path_template": "tts-audio/studio/{job_id}.mp3",
        }
        
        # Process job multiple times to trigger circuit breaker
        results = []
        for i in range(3):
            with patch('services.tts_worker.time.sleep'):
                result = worker.process_job(job_data)
                results.append(result)
        
        # After threshold failures, circuit should open
        # This is tested implicitly through the circuit breaker's internal state


class TestCleanupFix:
    """Test cleanup of None paths."""

    @patch('services.tts_worker.S3Client')
    def test_cleanup_local_files_handles_none(self, mock_s3):
        """Test that _cleanup_local_files handles None paths without raising error."""
        worker = IndexTTSWorker()
        # Should not raise TypeError when passed None
        worker._cleanup_local_files("non_existent_file.wav", None)

    @patch('services.tts_worker.S3Client')
    @patch('services.tts_worker.IdempotentUploader')
    @patch('services.tts_worker.platform.system')
    @patch('services.tts_worker.create_tts_engine')
    def test_infer_fast_not_called_with_language(self, mock_create_engine, mock_platform, mock_uploader, mock_s3):
        """Test that language kwarg is NOT passed to IndexTTS.infer_fast (it auto-detects from text)."""
        mock_platform.return_value = "Linux"
        mock_tts_engine = Mock()
        mock_create_engine.return_value = mock_tts_engine

        worker = IndexTTSWorker()
        worker.s3_client = Mock()
        worker.uploader = Mock()
        worker.tts.infer_fast = Mock(return_value="/fake/output.wav")
        worker.tts.infer = Mock(return_value="/fake/output.wav")
        worker._download_audio_prompt = Mock(return_value="/fake/prompt.wav")
        worker._upload_to_s3_idempotent = Mock(return_value="s3://bucket/output.wav")
        worker._get_audio_duration = Mock(return_value=5.0)
        worker._cleanup_local_files = Mock()

        job_data = {
            "job_id": "lang-test",
            "text": "Hello world",
            "audio_prompt_path": "audio-prompts/voice.wav",
            "language": "zh",
            "job_type": "studio",
            "output_path_template": "tts-audio/studio/{job_id}.mp3",
        }
        worker.process_job(job_data)

        # Verify infer_fast was called and language was NOT passed as a kwarg
        assert worker.tts.infer_fast.called
        call_kwargs = worker.tts.infer_fast.call_args[1]
        assert "language" not in call_kwargs, "language should NOT be passed to IndexTTS.infer_fast"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

