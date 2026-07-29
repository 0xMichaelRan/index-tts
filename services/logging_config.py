"""
Structured Logging Configuration for TTS Worker

Provides a consistent, scannable logging format with clear visual hierarchy:
- Section headers (STARTUP, CONNECTIONS, READY, etc.)
- Status indicators (✓ success, ✗ failure, ⚠ warning)
- Compact, aligned circuit breaker statistics
- Reduced metadata noise for console, full detail for file/JSON

This module configures Python's built-in logging with custom formatters
that adapt output based on destination (console vs. file).

Usage:
    from services.logging_config import get_logger, configure_logging

    # Configure logging (call once at startup)
    configure_logging(
        level=logging.INFO,
        use_file=False,  # Set True for file logging
        file_path="logs/worker.log"
    )

    # Get logger for a module
    logger = get_logger(__name__)

    # Use structured logging
    logger.section("STARTUP")
    logger.success("S3 client initialized")
    logger.failure("RabbitMQ connection failed: timeout")
    logger.info("Processing job: job_123")
"""

import logging
import sys
from datetime import datetime


class StructuredFormatter(logging.Formatter):
    """
    Structured log formatter with visual hierarchy and reduced noise.

    For console output:
    - Compact timestamp (HH:MM:SS)
    - Clean status indicators (✓, ✗, ⚠)
    - No module path repetition

    For file output:
    - Full timestamp with date
    - Full module paths
    - Consistent field ordering
    """

    # Color codes for terminal output (ANSI)
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[0m",  # Default
        "SUCCESS": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",  # Reset
    }

    # Status indicators
    INDICATORS = {
        "SUCCESS": "✓",
        "FAILURE": "✗",
        "WARNING": "⚠",
        "SECTION": "═",
    }

    def __init__(self, fmt=None, datefmt=None, use_color=True, compact=True):
        """
        Initialize formatter.

        Args:
            fmt: Format string (ignored if compact=True)
            datefmt: Date format string
            use_color: Enable color output
            compact: Use compact format for console
        """
        super().__init__(fmt, datefmt)
        self.use_color = use_color and sys.stdout.isatty()
        self.compact = compact

    def _format_timestamp(self, record: logging.LogRecord) -> str:
        """Format timestamp based on mode."""
        dt = datetime.fromtimestamp(record.created)
        if self.compact:
            return dt.strftime("%H:%M:%S")  # Compact: HH:MM:SS
        else:
            return dt.strftime("%Y-%m-%d %H:%M:%S")  # Full: YYYY-MM-DD HH:MM:SS

    def _get_level_indicator(self, level_name: str) -> str:
        """Get status indicator for log level."""
        # Check for custom status indicators in message
        if level_name == "INFO":
            # Detect custom status from message
            msg = self.msg if hasattr(self, "msg") else ""
            if "✓" in str(msg):
                return (
                    self.COLORS.get("SUCCESS", "") + "✓" + self.COLORS.get("RESET", "")
                )
            elif "✗" in str(msg):
                return self.COLORS.get("ERROR", "") + "✗" + self.COLORS.get("RESET", "")
            elif "⚠" in str(msg):
                return (
                    self.COLORS.get("WARNING", "") + "⚠" + self.COLORS.get("RESET", "")
                )

        return ""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with structured output."""
        # Store original message
        original_msg = record.getMessage()

        # Build formatted message
        timestamp = self._format_timestamp(record)
        level = record.levelname

        if self.compact:
            # Console format: [HH:MM:SS] [LEVEL] message
            color = self.COLORS.get(level, "")
            reset = self.COLORS.get("RESET", "") if self.use_color else ""
            color = color if self.use_color else ""

            formatted = f"{timestamp} {color}[{level:8s}]{reset} {original_msg}"
        else:
            # File format: full detail with module path
            formatted = f"{timestamp} - {record.name:30s} - {level:8s} - {original_msg}"

        # Add exception info if present
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)

        return formatted


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module.

    Args:
        name: Module name (typically __name__)

    Returns:
        Configured logger instance with custom methods
    """
    logger = logging.getLogger(name)

    # Add custom logging methods
    logger.success = lambda msg: logger.info(f"✓ {msg}")
    logger.failure = lambda msg: logger.error(f"✗ {msg}")
    logger.warning_icon = lambda msg: logger.warning(f"⚠ {msg}")
    logger.section = lambda title: logger.info(f"\n{'═' * 70}\n{title:^70}\n{'═' * 70}")
    logger.subsection = lambda title: logger.info(f"\n{title}\n{'-' * 70}")
    logger.divider = lambda: logger.info("─" * 70)

    return logger


def configure_logging(
    level: int = logging.INFO,
    use_file: bool = False,
    file_path: str = "logs/worker.log",
    use_color: bool = True,
) -> None:
    """
    Configure logging for the entire application.

    Sets up both console and optional file handlers with appropriate formatters.

    Args:
        level: Minimum logging level (logging.DEBUG, logging.INFO, etc.)
        use_file: Enable file logging
        file_path: Path to log file (if use_file=True)
        use_color: Enable color output for console
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler with compact formatter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = StructuredFormatter(
        use_color=use_color,
        compact=True,
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handler with full formatter (if enabled)
    if use_file:
        import os

        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)

        file_handler = logging.FileHandler(file_path)
        file_handler.setLevel(level)
        file_formatter = StructuredFormatter(
            use_color=False,
            compact=False,
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)


def log_circuit_breaker_stats(
    logger: logging.Logger,
    stats_dict: dict,
    title: str = "Circuit Breaker Status",
) -> None:
    """
    Log circuit breaker statistics in a compact, aligned table format.

    Args:
        logger: Logger instance
        stats_dict: Dictionary of {breaker_name: stats_dict}
        title: Section title
    """
    logger.info("")
    logger.info(f"{title:^70}")
    logger.info("─" * 70)

    # Header
    logger.info(
        f"{'Breaker':20s} {'State':12s} {'Success':10s} {'Failure':10s} {'Rate':10s}"
    )
    logger.info("─" * 70)

    # Rows
    for name, stats in stats_dict.items():
        state = stats.get("state", "UNKNOWN")
        success = stats.get("success_count", 0)
        failure = stats.get("failure_count", 0)
        error_rate = stats.get("error_rate", 0.0)

        logger.info(
            f"{name:20s} {state:12s} {success:10d} {failure:10d} {error_rate:9.1%}"
        )

    logger.info("─" * 70)


def log_startup_summary(
    logger: logging.Logger,
    platform: str,
    s3_storage_bucket: str,
    s3_output_bucket: str,
    rabbitmq_host: str,
    stats_dict: dict,
) -> None:
    """
    Log a comprehensive startup summary with all critical information.

    Disambiguates S3 buckets used for different purposes:
    - Storage bucket: audio prompts, voice recordings
    - Output bucket: TTS synthesis results

    Args:
        logger: Logger instance
        platform: Operating system (Darwin, Linux, etc.)
        s3_storage_bucket: S3 bucket for storage (audio prompts, voices)
        s3_output_bucket: S3 bucket for TTS output
        rabbitmq_host: RabbitMQ server hostname
        stats_dict: Circuit breaker statistics
    """
    logger.section("STARTUP COMPLETE")

    logger.info(f"Platform:         {platform}")
    logger.info(f"Storage Bucket:   {s3_storage_bucket}")
    logger.info(f"Output Bucket:    {s3_output_bucket}")
    logger.info(f"RabbitMQ Host:    {rabbitmq_host}")
    logger.info("")

    log_circuit_breaker_stats(logger, stats_dict, "Circuit Breakers")

    logger.section("READY")
    logger.info("Worker ready and listening for jobs...")
    logger.info("Press CTRL+C to stop")


def log_shutdown_summary(
    logger: logging.Logger,
    processed_count: int,
    stats_dict: dict,
) -> None:
    """
    Log shutdown summary with final statistics.

    Args:
        logger: Logger instance
        processed_count: Number of jobs processed
        stats_dict: Final circuit breaker statistics
    """
    logger.section("GRACEFUL SHUTDOWN")

    logger.info(f"Jobs Processed:   {processed_count}")
    logger.info("")

    log_circuit_breaker_stats(logger, stats_dict, "Final Circuit Breaker Stats")

    logger.info("")
    logger.info("Worker stopped successfully")
