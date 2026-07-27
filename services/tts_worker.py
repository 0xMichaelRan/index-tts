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
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import urlparse
from pathlib import Path

import pika
from dotenv import load_dotenv
from indextts.infer import create_tts_engine

# Load environment variables from .env file
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    load_dotenv(str(env_file))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class IndexTTSWorker:
    """Worker for processing TTS jobs from RabbitMQ queue."""

    def __init__(
        self,
        rabbitmq_host: str = "localhost",
        rabbitmq_port: int = 5672,
        rabbitmq_user: str = "guest",
        rabbitmq_password: str = "guest",
        s3_bucket: str = "tts-output",
        s3_region: str = "us-east-1",
    ):
        """
        Initialize the TTS worker.

        Args:
            rabbitmq_host: RabbitMQ server hostname
            rabbitmq_port: RabbitMQ server port
            rabbitmq_user: RabbitMQ username
            rabbitmq_password: RabbitMQ password
            s3_bucket: S3 bucket for storing output audio
            s3_region: AWS region for S3
        """
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.rabbitmq_user = rabbitmq_user
        self.rabbitmq_password = rabbitmq_password
        self.s3_bucket = s3_bucket
        self.s3_region = s3_region

        # Initialize TTS engine
        logger.info(f"Running on: {platform.system()}")
        self._init_tts_engine()

        # Placeholder for RabbitMQ connection
        self.connection = None
        self.channel = None

    def _init_tts_engine(self):
        """Initialize the appropriate TTS engine based on platform."""
        if platform.system() == "Darwin":
            logger.info(">> Initializing macOS native TTS engine")
            self.tts = create_tts_engine(use_native_macos=True, language="en-US")
        else:
            logger.info(">> Initializing IndexTTS GPU inference engine")
            self.tts = create_tts_engine(
                use_native_macos=False,
                cfg_path="checkpoints/config.yaml",
                model_dir="checkpoints",
                is_fp16=True,
                use_cuda_kernel=False,
            )

    def connect_rabbitmq(self):
        """
        Connect to RabbitMQ using the RABBITMQ_URL from environment.
        Supports CloudAMQP and standard RabbitMQ URLs.
        """
        try:
            rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
            logger.info(f"Connecting to RabbitMQ: {rabbitmq_url.split('@')[1] if '@' in rabbitmq_url else 'local'}")

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
            
            logger.info("✓ Connected to RabbitMQ")

        except Exception as e:
            logger.error(f"✗ Failed to connect to RabbitMQ: {str(e)}")
            raise

    def disconnect_rabbitmq(self):
        """Safely close RabbitMQ connection."""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                logger.info("✓ Disconnected from RabbitMQ")
        except Exception as e:
            logger.error(f"Error disconnecting from RabbitMQ: {str(e)}")

    def process_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single TTS job.

        Args:
            job_data: Job message containing:
                - job_id: Unique job identifier
                - text: Text to synthesize
                - audio_prompt: Optional path/URL to audio prompt file
                - voice_params: Optional voice parameters (rate, pitch, volume)

        Returns:
            Result dictionary with:
                - job_id: Original job ID
                - status: "completed" or "failed"
                - output_s3_path: S3 path to generated audio (if successful)
                - error: Error message (if failed)
        """
        job_id = job_data.get("job_id")
        text = job_data.get("text", "")
        audio_prompt = job_data.get("audio_prompt")
        voice_params = job_data.get("voice_params", {})

        logger.info(f"[JOB {job_id}] Processing TTS request")

        try:
            # Step 1: Synthesize audio
            logger.info(f"[JOB {job_id}] Synthesizing audio...")
            output_path = self._synthesize_audio(
                job_id, text, audio_prompt, voice_params
            )

            # Step 2: Upload to S3
            logger.info(f"[JOB {job_id}] Uploading to S3...")
            s3_path = self._upload_to_s3(job_id, output_path)

            # Step 3: Clean up local file
            logger.info(f"[JOB {job_id}] Cleaning up local files...")
            self._cleanup_local_files(output_path)

            result = {
                "job_id": job_id,
                "status": "completed",
                "output_s3_path": s3_path,
                "timestamp": datetime.now().isoformat(),
            }
            logger.info(f"[JOB {job_id}] Job completed successfully")
            return result

        except Exception as e:
            logger.error(f"[JOB {job_id}] Error processing job: {str(e)}")
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def _synthesize_audio(
        self,
        job_id: str,
        text: str,
        audio_prompt: Optional[str],
        voice_params: Dict[str, Any],
    ) -> str:
        """
        Synthesize audio using TTS engine.
        TODO: Implement actual synthesis logic
        """
        output_dir = os.path.join("outputs", "tts_output")
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        output_filename = f"{job_id}_{timestamp}.wav"
        output_path = os.path.join(output_dir, output_filename)

        logger.info(f"[JOB {job_id}] [PLACEHOLDER] Synthesizing to {output_path}")

        # Placeholder: Just create an empty file
        # In real implementation:
        # if platform.system() == "Darwin":
        #     self.tts.infer(audio_prompt=None, text=text, output_path=output_path, ...)
        # else:
        #     self.tts.infer_fast(audio_prompt=audio_prompt, text=text, output_path=output_path, ...)

        with open(output_path, "wb") as f:
            f.write(b"PLACEHOLDER_AUDIO_DATA")

        return output_path

    def _upload_to_s3(self, job_id: str, local_path: str) -> str:
        """
        Upload synthesized audio to S3.
        TODO: Implement actual S3 upload using boto3
        """
        s3_key = f"tts-output/{job_id}/{os.path.basename(local_path)}"
        logger.info(
            f"[JOB {job_id}] [PLACEHOLDER] Uploading to s3://{self.s3_bucket}/{s3_key}"
        )

        # Placeholder S3 path
        # In real implementation:
        # s3_client = boto3.client("s3", region_name=self.s3_region)
        # s3_client.upload_file(local_path, self.s3_bucket, s3_key)

        return f"s3://{self.s3_bucket}/{s3_key}"

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
        Publish job result back to RabbitMQ tts_results queue.
        """
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
            logger.info(f"✓ Published result for job {result.get('job_id')}")
        except Exception as e:
            logger.error(f"✗ Failed to publish result: {str(e)}")

    def start(self):
        """Start the worker and begin consuming jobs from RabbitMQ."""
        logger.info("Starting IndexTTS Worker...")
        try:
            self.connect_rabbitmq()

            def callback(ch, method, properties, body):
                """Handle incoming job message."""
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
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                except Exception as e:
                    logger.error(f"Error processing job: {str(e)}")
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            # Set QoS to process one message at a time
            self.channel.basic_qos(prefetch_count=1)

            # Set up consumer
            self.channel.basic_consume(
                queue="tts_jobs",
                on_message_callback=callback,
                auto_ack=False,
            )

            logger.info("✓ Worker ready and listening for jobs...")
            logger.info("Press CTRL+C to stop")
            
            # Start consuming
            self.channel.start_consuming()

        except KeyboardInterrupt:
            logger.info("\nShutting down worker...")
        except Exception as e:
            logger.error(f"Fatal error: {str(e)}")
        finally:
            self.disconnect_rabbitmq()


if __name__ == "__main__":
    worker = IndexTTSWorker(
        rabbitmq_host=os.getenv("RABBITMQ_HOST", "localhost"),
        rabbitmq_port=int(os.getenv("RABBITMQ_PORT", 5672)),
        rabbitmq_user=os.getenv("RABBITMQ_USER", "guest"),
        rabbitmq_password=os.getenv("RABBITMQ_PASSWORD", "guest"),
        s3_bucket=os.getenv("S3_BUCKET", "tts-output"),
        s3_region=os.getenv("AWS_REGION", "us-east-1"),
    )
    worker.start()
