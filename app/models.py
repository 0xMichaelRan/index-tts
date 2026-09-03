"""
SQLAlchemy models for TTS synthesis cache.

This module defines the database schema for caching TTS synthesis results
to enable reuse of base audio when the same (text, voice) combination is
requested with different speed ratios.
"""

from sqlalchemy import (
    Boolean,
    Column,
    String,
    Text,
    Float,
    Integer,
    BigInteger,
    Numeric,
    DateTime,
    CheckConstraint,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TTSSynthesisCache(Base):
    """
    TTS synthesis cache for storing base audio files.

    Enables reuse of synthesized audio when same (text, voice)
    is requested with different speed ratios. The cache stores
    base audio synthesized at ratio=1.0, which can then be
    time-stretched to match the requested ratio.

    Key Design:
    - cache_key: SHA256 hash of (text + audio_prompt_path)
    - Base audio always synthesized at ratio=1.0
    - Time-stretching applied separately for different ratios
    - LRU eviction based on last_accessed_at

    Performance Impact:
    - Cache hit: ~1-2s (copy + time-stretch)
    - Cache miss: ~5-10s (full synthesis)
    - Savings: 65-80% for cache hits
    """

    __tablename__ = "tts_synthesis_cache"

    # Primary Key
    cache_key = Column(
        String(64), primary_key=True, comment="SHA256 hash of text + audio_prompt_path"
    )

    # Content identification
    text = Column(Text, nullable=False, comment="Full text that was synthesized")
    audio_prompt_path = Column(
        String(512), nullable=False, comment="S3 path to voice prompt file"
    )
    text_hash = Column(
        String(64),
        nullable=False,
        index=True,
        comment="SHA256 hash of text only (for indexing)",
    )

    # File locations
    base_audio_local_path = Column(
        String(1024), nullable=False, comment="Local filesystem path to cached WAV file"
    )
    base_audio_s3_path = Column(
        String(1024),
        nullable=True,
        comment="[UNUSED] Reserved for future S3 backup (cache is currently local-filesystem based for performance)",
    )

    # Audio metadata
    audio_duration_seconds = Column(
        Float, nullable=False, comment="Duration of base audio in seconds"
    )
    sample_rate = Column(Integer, default=24000, comment="Audio sample rate (Hz)")
    audio_format = Column(String(10), default="wav", comment="Audio file format")
    file_size_bytes = Column(BigInteger, nullable=True, comment="File size in bytes")

    # Performance metrics
    synthesis_duration_ms = Column(
        Integer, nullable=False, comment="Time taken to synthesize (milliseconds)"
    )
    hit_count = Column(
        Integer, default=0, comment="Number of times this cache entry was reused"
    )

    # Timestamps
    last_accessed_at = Column(
        DateTime(timezone=True),
        default=func.now(),
        comment="Last time this cache entry was accessed",
    )
    created_at = Column(
        DateTime(timezone=True),
        default=func.now(),
        comment="When this cache entry was created",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        comment="When this cache entry was last updated",
    )

    # Optional metadata
    language = Column(
        String(10), nullable=True, comment="Language code (e.g., 'en', 'zh')"
    )
    tts_engine = Column(
        String(50), default="IndexTTS-1.5", comment="TTS engine version"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint("audio_duration_seconds > 0", name="valid_duration"),
        CheckConstraint("LENGTH(base_audio_local_path) > 0", name="valid_file_path"),
        CheckConstraint("hit_count >= 0", name="valid_hit_count"),
    )

    def __repr__(self):
        """String representation for debugging."""
        return (
            f"<TTSSynthesisCache("
            f"cache_key={self.cache_key[:16]}..., "
            f"hit_count={self.hit_count}, "
            f"duration={self.audio_duration_seconds:.2f}s"
            f")>"
        )

    def to_dict(self):
        """Convert model to dictionary for JSON serialization."""
        return {
            "cache_key": self.cache_key,
            "text": self.text,
            "audio_prompt_path": self.audio_prompt_path,
            "text_hash": self.text_hash,
            "base_audio_local_path": self.base_audio_local_path,
            "base_audio_s3_path": self.base_audio_s3_path,
            "audio_duration_seconds": self.audio_duration_seconds,
            "sample_rate": self.sample_rate,
            "audio_format": self.audio_format,
            "file_size_bytes": self.file_size_bytes,
            "synthesis_duration_ms": self.synthesis_duration_ms,
            "hit_count": self.hit_count,
            "last_accessed_at": self.last_accessed_at.isoformat()
            if self.last_accessed_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "language": self.language,
            "tts_engine": self.tts_engine,
        }


class TTSJob(Base):
    """TTS job tracking for retry detection, analytics, and lifecycle management."""

    __tablename__ = "tts_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)  # ttsId
    job_id = Column(Integer, nullable=False, unique=True, index=True)
    job_type = Column(String(20), nullable=False)
    status = Column(
        String(20), nullable=False, default="queued", server_default="queued"
    )

    # Test flag
    is_test = Column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
        index=True,
        comment="Whether this is a test job (isTest=true in message)",
    )

    # TTS parameters
    text = Column(Text, nullable=True)
    audio_prompt_path = Column(Text, nullable=True)
    language = Column(String(10), nullable=True)
    ratio = Column(Numeric(3, 1), nullable=True)

    # Results
    cache_key = Column(
        String(64),
        ForeignKey("tts_synthesis_cache.cache_key", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    audio_path = Column(Text, nullable=True)
    alignment_path = Column(Text, nullable=True)
    audio_duration_seconds = Column(Numeric(10, 2), nullable=True)
    synthesis_duration_seconds = Column(
        Numeric(10, 2), nullable=False, default=0.0, server_default="0.0"
    )

    # Observability and timing breakdown
    cache_hit = Column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
        comment="Whether synthesis result came from cache",
    )
    alignment_duration_seconds = Column(
        Numeric(10, 2),
        nullable=True,
        comment="Time spent on forced alignment (seconds)",
    )
    time_stretched = Column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
        comment="Whether audio was time-stretched (ratio != 1.0)",
    )
    source_ratio = Column(
        Numeric(3, 1),
        nullable=True,
        comment="Original synthesis ratio (always 1.0 for cache)",
    )
    target_ratio = Column(
        Numeric(3, 1),
        nullable=True,
        comment="Target playback speed ratio requested",
    )

    # Error tracking
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Timestamp when job was archived",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_tts_jobs_status",
        ),
        CheckConstraint(
            "job_type IN ('studio', 'playground', 'rem')",
            name="ck_tts_jobs_job_type",
        ),
        Index("idx_tts_jobs_created_status", "created_at", "status"),
    )

    def __repr__(self):
        return f"<TTSJob(id={self.id}, job_id={self.job_id}, status='{self.status}')>"

    def __init__(self, **kwargs):
        """Initialize TTSJob with defaults for observability fields."""
        # Set defaults for fields that need Python-level defaults
        if "cache_hit" not in kwargs:
            kwargs["cache_hit"] = False
        if "time_stretched" not in kwargs:
            kwargs["time_stretched"] = False
        super().__init__(**kwargs)
