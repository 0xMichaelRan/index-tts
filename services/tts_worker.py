"""
IndexTTS RabbitMQ Worker
24/7 background worker for TTS synthesis from RabbitMQ queue.
- Consumes TTS requests from RabbitMQ
- Synthesizes audio using IndexTTS engine
- Uploads results to S3
- Updates status back to RabbitMQ
"""

import os
import json
import platform
import logging
import time
import signal
import wave
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import urlparse
from pathlib import Path

import pika
from dotenv import load_dotenv
from indextts.infer import create_tts_engine

from services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    get_circuit_breaker,
    get_all_circuit_breaker_stats,
)
from services.s3_config import S3Client, S3ConfigError
from services.idempotent_upload import IdempotentUploader
from services.logging_config import configure_logging, get_logger, log_startup_summary, log_shutdown_summary

# Load environment variables from .env file
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    load_dotenv(str(env_file))

# Configure structured logging
configure_logging(
    level=logging.INFO,
    use_file=False,  # Set to True to enable file logging
    use_color=True,
)
logger = get_logger(__name__)


class IndexTTSWorker:
    """Worker for processing TTS jobs from RabbitMQ queue."""

    def __init__(
        self,
        rabbitmq_host: str = "localhost",
        rabbitmq_port: int = 5672,
        rabbitmq_user: str = "guest",
        rabbitmq_password: str = "guest",
        s3_storage_bucket: str = "studio",
        s3_output_bucket: str = "tts-output",
        s3_region: str = "us-east-1",
    ):
        """
        Initialize the TTS worker.

        Args:
            rabbitmq_host: RabbitMQ server hostname
            rabbitmq_port: RabbitMQ server port
            rabbitmq_user: RabbitMQ username
            rabbitmq_password: RabbitMQ password
            s3_storage_bucket: S3 bucket for audio prompts/voices (from S3_BUCKET_NAME)
            s3_output_bucket: S3 bucket for TTS synthesis output (from S3_OUTPUT_BUCKET)
            s3_region: AWS region for S3
        """
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.rabbitmq_user = rabbitmq_user
        self.rabbitmq_password = rabbitmq_password
        self.s3_storage_bucket = s3_storage_bucket
        self.s3_output_bucket = s3_output_bucket
        self.s3_region = s3_region
        self.platform = platform.system()

        logger.section("STARTUP")
        logger.info(f"Platform:         {self.platform}")

        # Initialize TTS engine
        self._init_tts_engine()
        logger.success("TTS engine initialized")

        # Initialize S3 client
        try:
            self.s3_client = S3Client()
            logger.success("S3 client initialized")
        except Exception as e:
            logger.warning_icon(f"S3 client initialization failed: {e}. Will retry on first use.")
            self.s3_client = None
        
        # Initialize idempotent uploader
        self.uploader = None
        try:
            self.uploader = IdempotentUploader(self.s3_client)
            logger.success("Idempotent uploader initialized")
        except Exception as e:
            logger.warning_icon(f"Uploader initialization failed: {e}. Will initialize on first use.")
            self.uploader = None

        # Initialize circuit breakers
        logger.subsection("Initializing Circuit Breakers")
        self.s3_breaker = get_circuit_breaker(
            name="S3Download",
            failure_threshold=5,
            reset_timeout=60,
            half_open_max_calls=3,
            success_threshold=2,
        )
        
        self.tts_breaker = get_circuit_breaker(
            name="IndexTTS",
            failure_threshold=3,
            reset_timeout=30,
            half_open_max_calls=2,
            success_threshold=2,
        )

        # Placeholder for RabbitMQ connection
        self.connection = None
        self.channel = None
        
        # Tracking for idempotent operations
        self._processed_jobs = set()  # Track completed job IDs for deduplication
        
        # Graceful shutdown support
        self._shutdown_requested = False
        self._setup_signal_handlers()

    def _init_tts_engine(self):
        """Initialize the appropriate TTS engine based on platform."""
        if self.platform == "Darwin":
            logger.info("Initializing macOS native TTS engine (language: en-US)")
            self.tts = create_tts_engine(use_native_macos=True, language="en-US")
        else:
            logger.info("Initializing IndexTTS GPU inference engine")
            self.tts = create_tts_engine(
                use_native_macos=False,
                cfg_path="checkpoints/config.yaml",
                model_dir="checkpoints",
                is_fp16=True,
                use_cuda_kernel=False,
            )
    
    def _setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            """Handle shutdown signals."""
            signal_name = signal.Signals(signum).name
            logger.info(f"\n{signal_name} received, initiating graceful shutdown...")
            self._shutdown_requested = True
            
            # Stop consuming new messages
            if self.channel and not self.channel.is_closed:
                try:
                    self.channel.stop_consuming()
                    logger.info("Stopped consuming new messages")
                except Exception as e:
                    logger.warning_icon(f"Error stopping consumer: {e}")
        
        # Register handlers for SIGTERM and SIGINT
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        logger.success("Signal handlers registered (SIGTERM, SIGINT)")

    def connect_rabbitmq(self):
        """
        Connect to RabbitMQ using the RABBITMQ_URL from environment.
        Supports CloudAMQP and standard RabbitMQ URLs.
        """
        try:
            rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
            host_display = rabbitmq_url.split('@')[1] if '@' in rabbitmq_url else 'localhost'
            logger.subsection(f"Connecting to RabbitMQ ({host_display})")

            # Parse the URL manually to extract components
            parsed = urlparse(rabbitmq_url)
            
            credentials = pika.PlainCredentials(
                username=parsed.username or "guest",
                password=parsed.password or "guest",
            )
            
            connection_params = pika.ConnectionParameters(
                host=parsed.hostname or "localhost",
                port=parsed.port or 5672,
                virtual_host=parsed.path.lstrip("/") or "/",
                credentials=credentials,
                connection_attempts=3,
                retry_delay=2,
                heartbeat=600,
                blocked_connection_timeout=300,
            )

            self.connection = pika.BlockingConnection([connection_params])
            self.channel = self.connection.channel()
            
            # Declare the queue (idempotent if it already exists)
            self.channel.queue_declare(queue="tts_jobs", durable=True)
            
            logger.success("Connected to RabbitMQ")

        except Exception as e:
            logger.failure(f"Failed to connect to RabbitMQ: {str(e)}")
            raise

    def disconnect_rabbitmq(self):
        """Safely close RabbitMQ connection."""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                logger.success("Disconnected from RabbitMQ")
        except Exception as e:
            logger.warning_icon(f"Error disconnecting from RabbitMQ: {str(e)}")

    def process_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single TTS job with circuit breaker protection.

        Args:
            job_data: Job message containing:
                - job_id: Unique job identifier
                - text: Text to synthesize
                - audio_prompt_path: S3 path to audio prompt file
                - language: Language code
                - job_type: "studio" or "playground"
                - output_path_template: S3 output path template

        Returns:
            Result dictionary with:
                - job_id: Original job ID
                - status: "completed" or "failed"
                - audio_path: S3 path to generated audio (if successful)
                - audio_duration_seconds: Duration of synthesized audio
                - synthesis_duration_seconds: Time taken for synthesis
                - error_code: Error code (if failed)
                - error: Error message (if failed)
                - retry_count: Number of retries attempted
        """
        job_id = job_data.get("job_id")
        text = job_data.get("text", "")
        audio_prompt_path = job_data.get("audio_prompt_path")
        language = job_data.get("language", "en")
        job_type = job_data.get("job_type", "studio")
        output_path_template = job_data.get("output_path_template")
        
        retry_count = 0
        max_retries = 3
        
        logger.info(f"[JOB {job_id}] Processing TTS request (type: {job_type}, language: {language})")
        
        # Check for duplicate processing
        if job_id in self._processed_jobs:
            logger.warning(f"[JOB {job_id}] Already processed, skipping")
            return {
                "job_id": job_id,
                "status": "completed",
                "note": "duplicate_skipped",
                "timestamp": datetime.now().isoformat(),
            }
        
        synthesis_start = time.time()
        local_audio_prompt = None
        local_output = None

        # Retry loop for transient failures
        while retry_count < max_retries:
            try:
                # Step 1: Download audio prompt from S3 with circuit breaker
                logger.info(f"[JOB {job_id}] Downloading audio prompt from S3...")
                try:
                    with self.s3_breaker:
                        local_audio_prompt = self._download_audio_prompt(
                            job_id, audio_prompt_path
                        )
                except CircuitBreakerError:
                    error_msg = "S3 circuit breaker is open - service unavailable"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "job_id": job_id,
                        "status": "failed",
                        "error_code": "S3_CIRCUIT_OPEN",
                        "error_message": error_msg,
                        "retry_count": retry_count,
                        "timestamp": datetime.now().isoformat(),
                    }

                # Step 2: Synthesize audio with circuit breaker
                logger.info(f"[JOB {job_id}] Synthesizing audio...")
                try:
                    with self.tts_breaker:
                        local_output = self._synthesize_audio(
                            job_id, text, local_audio_prompt, language
                        )
                except CircuitBreakerError:
                    error_msg = "IndexTTS circuit breaker is open - service unavailable"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "job_id": job_id,
                        "status": "failed",
                        "error_code": "TTS_CIRCUIT_OPEN",
                        "error_message": error_msg,
                        "retry_count": retry_count,
                        "timestamp": datetime.now().isoformat(),
                    }

                # Step 3: Upload to S3 with idempotent retry
                logger.info(f"[JOB {job_id}] Uploading to S3...")
                output_s3_path = output_path_template.format(job_id=job_id)
                
                try:
                    with self.s3_breaker:
                        audio_path = self._upload_to_s3_idempotent(
                            job_id, local_output, output_s3_path
                        )
                except CircuitBreakerError:
                    error_msg = "S3 circuit breaker is open during upload"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "job_id": job_id,
                        "status": "failed",
                        "error_code": "S3_UPLOAD_CIRCUIT_OPEN",
                        "error_message": error_msg,
                        "retry_count": retry_count,
                        "timestamp": datetime.now().isoformat(),
                    }

                # Step 4: Calculate audio duration
                audio_duration = self._get_audio_duration(local_output)
                
                synthesis_duration = time.time() - synthesis_start
                
                # Mark as processed
                self._processed_jobs.add(job_id)
                
                result = {
                    "job_type": job_type,
                    "job_id": job_id,
                    "status": "completed",
                    "audio_path": audio_path,
                    "audio_duration_seconds": audio_duration,
                    "synthesis_duration_seconds": round(synthesis_duration, 2),
                    "retry_count": retry_count,
                    "timestamp": datetime.now().isoformat(),
                }
                logger.info(
                    f"[JOB {job_id}] Job completed successfully in {synthesis_duration:.2f}s"
                )
                return result

            except (S3ConfigError, OSError, IOError) as e:
                # Retryable errors
                retry_count += 1
                if retry_count < max_retries:
                    delay = 2 ** retry_count  # Exponential backoff: 2, 4, 8 seconds
                    logger.warning(
                        f"[JOB {job_id}] Attempt {retry_count}/{max_retries} failed: {str(e)}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"[JOB {job_id}] All {max_retries} attempts failed: {str(e)}"
                    )
                    return {
                        "job_type": job_type,
                        "job_id": job_id,
                        "status": "failed",
                        "error_code": "RETRYABLE_ERROR_EXHAUSTED",
                        "error_message": str(e),
                        "retry_count": retry_count,
                        "timestamp": datetime.now().isoformat(),
                    }
            
            except Exception as e:
                # Non-retryable errors
                logger.error(f"[JOB {job_id}] Non-retryable error: {str(e)}")
                return {
                    "job_type": job_type,
                    "job_id": job_id,
                    "status": "failed",
                    "error_code": "NON_RETRYABLE_ERROR",
                    "error_message": str(e),
                    "retry_count": retry_count,
                    "timestamp": datetime.now().isoformat(),
                }
            
            finally:
                # Always clean up local files
                if local_audio_prompt or local_output:
                    self._cleanup_local_files(local_audio_prompt, local_output)

    def _download_audio_prompt(
        self,
        job_id: str,
        audio_prompt_path: str,
    ) -> str:
        """
        Download audio prompt from S3 storage bucket with retry logic.
        
        Args:
            job_id: Job identifier
            audio_prompt_path: S3 path to audio prompt
            
        Returns:
            Local file path to downloaded audio
            
        Raises:
            S3ConfigError: If download fails after retries
        """
        if not self.s3_client:
            self.s3_client = S3Client()
        
        # Create temp directory for downloads
        temp_dir = os.path.join("outputs", "temp", job_id)
        os.makedirs(temp_dir, exist_ok=True)
        
        local_path = os.path.join(temp_dir, os.path.basename(audio_prompt_path))
        
        # Download from storage bucket (where voices are stored)
        self.s3_client.download_file(
            remote_path=audio_prompt_path,
            local_path=local_path,
            bucket_type="storage",
            max_retries=3,
        )
        
        logger.info(f"[JOB {job_id}] Downloaded audio prompt to {local_path}")
        return local_path

    def _synthesize_audio(
        self,
        job_id: str,
        text: str,
        audio_prompt: Optional[str],
        language: str,
    ) -> str:
        """
        Synthesize audio using TTS engine.
        
        Args:
            job_id: Job identifier
            text: Text to synthesize
            audio_prompt: Local path to audio prompt file
            language: Language code
            
        Returns:
            Local path to synthesized audio
            
        Raises:
            Exception: If synthesis fails
        """
        output_dir = os.path.join("outputs", "tts_output", job_id)
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        output_filename = f"{job_id}_{timestamp}.wav"
        output_path = os.path.join(output_dir, output_filename)

        logger.info(f"[JOB {job_id}] Synthesizing to {output_path}")

        # Platform-specific synthesis
        if platform.system() == "Darwin":
            # macOS native TTS
            self.tts.infer(
                audio_prompt=None,
                text=text,
                output_path=output_path,
                language=language,
            )
        else:
            # IndexTTS GPU inference
            self.tts.infer_fast(
                audio_prompt=audio_prompt,
                text=text,
                output_path=output_path,
                language=language,
            )
        
        logger.info(f"[JOB {job_id}] Synthesis complete: {output_path}")
        return output_path

    def _upload_to_s3_idempotent(
        self,
        job_id: str,
        local_path: str,
        remote_path: str,
    ) -> str:
        """
        Upload synthesized audio to S3 with idempotent retry.
        
        Implements idempotent retry by checking if file already exists
        with matching job_id metadata tag before uploading.
        
        Features:
        - Check if file already uploaded (idempotent check)
        - Skip upload if file exists with matching job_id
        - Exponential backoff retry on failure (base: 2s, multiplier: 2)
        - Metadata tagging with job_id and status
        - Partial failure recovery support
        
        Args:
            job_id: Job identifier
            local_path: Local file path
            remote_path: S3 destination path
            
        Returns:
            S3 path (remote_path)
            
        Raises:
            S3ConfigError: If upload fails after retries
        """
        if not self.uploader:
            self.uploader = IdempotentUploader(self.s3_client)
        
        logger.info(f"[JOB {job_id}] Starting idempotent S3 upload")
        
        try:
            # Use idempotent uploader with retry logic
            s3_path = self.uploader.upload_with_retry(
                job_id=job_id,
                local_path=local_path,
                remote_path=remote_path,
                verify_integrity=True,
            )
            
            logger.info(f"[JOB {job_id}] Upload completed: {s3_path}")
            return s3_path
            
        except S3ConfigError as e:
            logger.error(f"[JOB {job_id}] Upload failed: {e}")
            raise

    def _get_audio_duration(self, audio_path: str) -> float:
        """
        Get duration of audio file in seconds.
        
        Supports WAV files using the wave module.
        
        Args:
            audio_path: Path to audio file (must be .wav format)
            
        Returns:
            Duration in seconds
            
        Raises:
            FileNotFoundError: If audio file doesn't exist
            ValueError: If file is not a valid WAV file
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        try:
            with wave.open(audio_path, 'r') as audio_file:
                frames = audio_file.getnframes()
                rate = audio_file.getframerate()
                duration = frames / float(rate)
                return duration
        except wave.Error as e:
            raise ValueError(f"Invalid WAV file {audio_path}: {e}")
        except Exception as e:
            logger.warning(f"Could not read audio duration from {audio_path}: {e}")
            # Fallback: estimate based on file size (very rough approximation)
            # WAV at 24kHz, 16-bit mono: ~48000 bytes/sec
            file_size = os.path.getsize(audio_path)
            estimated_duration = file_size / 48000.0
            logger.info(f"Using estimated duration: {estimated_duration:.2f}s based on file size")
            return estimated_duration

    def _cleanup_local_files(self, *paths: str):
        """Remove local temporary files."""
        for path in paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.debug(f"Removed: {path}")
            except Exception as e:
                logger.warning(f"Failed to remove {path}: {str(e)}")

    def publish_result(self, result: Dict[str, Any]):
        """
        Publish job result back to RabbitMQ tts_results queue with retry.
        
        Implements exponential backoff retry for RabbitMQ ack failures.
        If all retries fail after S3 upload success, triggers partial failure
        recovery and logs critical error for manual intervention.
        
        Partial Failure Handling:
        - If S3 upload succeeded but RabbitMQ publish fails
        - File is safely stored in S3
        - Manual intervention required to update database
        - Recovery metadata is logged for debugging
        
        Args:
            result: Job result dictionary with status and metadata
        """
        max_retries = 3
        retry_count = 0
        job_id = result.get("job_id")
        
        while retry_count < max_retries:
            try:
                self.channel.basic_publish(
                    exchange="",
                    routing_key="tts_results",
                    body=json.dumps(result),
                    properties=pika.BasicProperties(
                        delivery_mode=pika.DeliveryMode.Persistent,
                        content_type="application/json",
                    ),
                )
                logger.info(f"✓ Published result for job {job_id}")
                return
                
            except Exception as e:
                retry_count += 1
                if retry_count < max_retries:
                    delay = 2 ** retry_count  # Exponential backoff: 2, 4, 8 seconds
                    logger.warning(
                        f"[JOB {job_id}] Failed to publish result (attempt {retry_count}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    # All retries exhausted - check for partial failure scenario
                    logger.critical(
                        f"[JOB {job_id}] ✗ Failed to publish result after {max_retries} attempts: {e}"
                    )
                    
                    # If result contains audio_path, S3 upload likely succeeded
                    if result.get("status") == "completed" and result.get("audio_path"):
                        logger.critical(
                            f"[JOB {job_id}] PARTIAL FAILURE DETECTED: "
                            f"S3 upload succeeded but RabbitMQ publish failed"
                        )
                        
                        # Trigger recovery for partial failure
                        if self.uploader:
                            recovery_data = self.uploader.handle_partial_failure(
                                job_id=job_id,
                                remote_path=result.get("audio_path"),
                                error=e,
                            )
                            logger.critical(f"Recovery data: {json.dumps(recovery_data, indent=2)}")
                    
                    # Do not raise - we've done our best, avoid requeueing the original message

    def start(self):
        """Start the worker and begin consuming jobs from RabbitMQ."""
        try:
            self.connect_rabbitmq()
            
            # Log connection summary
            logger.subsection("CONNECTIONS")
            logger.info(f"S3 Storage Bucket:   {self.s3_storage_bucket}")
            logger.info(f"S3 Output Bucket:    {self.s3_output_bucket}")
            logger.info("")
            
            # Log circuit breaker status
            cb_stats = get_all_circuit_breaker_stats()
            log_startup_summary(
                logger,
                platform=self.platform,
                s3_storage_bucket=self.s3_storage_bucket,
                s3_output_bucket=self.s3_output_bucket,
                rabbitmq_host=self.rabbitmq_host,
                stats_dict=cb_stats,
            )

            def callback(ch, method, properties, body):
                """Handle incoming job message."""
                # Check for shutdown request before processing
                if self._shutdown_requested:
                    logger.info("Shutdown requested, rejecting new message")
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                    return
                
                job_data = None
                try:
                    job_data = json.loads(body)
                    job_id = job_data.get("job_id")
                    logger.info(f"[JOB {job_id}] Received from queue")

                    result = self.process_job(job_data)
                    self.publish_result(result)

                    # Acknowledge message only after successful processing
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    logger.info(f"[JOB {job_id}] Acknowledged")
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON in message: {str(e)}")
                    # Reject without requeue - invalid messages go to DLQ
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                    
                except CircuitBreakerError as e:
                    logger.error(f"Circuit breaker open: {str(e)}")
                    # Requeue when circuit breaker is open - might succeed later
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                    
                except Exception as e:
                    logger.error(f"Error processing job: {str(e)}")
                    if job_data:
                        job_id = job_data.get("job_id")
                        logger.error(f"[JOB {job_id}] Processing failed, sending to DLQ")
                    # Reject without requeue - failed jobs go to DLQ after retries
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            # Set QoS to process one message at a time
            self.channel.basic_qos(prefetch_count=1)

            # Set up consumer
            self.channel.basic_consume(
                queue="tts_jobs",
                on_message_callback=callback,
                auto_ack=False,
            )

            # Start consuming
            self.channel.start_consuming()

        except KeyboardInterrupt:
            logger.info("\nShutting down worker (KeyboardInterrupt)...")
            self._shutdown_requested = True
            
        except Exception as e:
            logger.failure(f"Fatal error: {str(e)}")
            self._shutdown_requested = True
            
        finally:
            # Graceful shutdown: Print statistics
            if self._shutdown_requested:
                cb_stats = get_all_circuit_breaker_stats()
                log_shutdown_summary(
                    logger,
                    processed_count=len(self._processed_jobs),
                    stats_dict=cb_stats,
                )
            
            self.disconnect_rabbitmq()


if __name__ == "__main__":
    worker = IndexTTSWorker(
        rabbitmq_host=os.getenv("RABBITMQ_HOST", "localhost"),
        rabbitmq_port=int(os.getenv("RABBITMQ_PORT", 5672)),
        rabbitmq_user=os.getenv("RABBITMQ_USER", "guest"),
        rabbitmq_password=os.getenv("RABBITMQ_PASSWORD", "guest"),
        s3_storage_bucket=os.getenv("S3_BUCKET_NAME", "studio"),
        s3_output_bucket=os.getenv("S3_OUTPUT_BUCKET", "tts-output"),
        s3_region=os.getenv("S3_REGION", "us-east-1"),
    )
    worker.start()
