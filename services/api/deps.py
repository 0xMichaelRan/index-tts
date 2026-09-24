from __future__ import annotations

import platform
from typing import Any, Optional
from fastapi import Depends, HTTPException

from indextts.infer import create_tts_engine
from services.common.logging_config import get_logger
from services.common.worker_config import WorkerConfig
from services.storage.s3_config import S3Client, S3ConfigError
from services.storage.storage_manager import StorageManager
from services.tts.cache_manager import CacheManager
from services.tts.synthesis_pipeline import SynthesisPipeline
from services.vox.pipeline import VoxRenderPipeline

logger = get_logger(__name__)

# Global singletons for caching lazy instances
_s3_client: Optional[S3Client] = None
_storage_manager: Optional[StorageManager] = None
_tts_engine: Any = None
_cache_manager: Optional[CacheManager] = None
_tts_pipeline: Optional[SynthesisPipeline] = None
_vox_pipeline: Optional[VoxRenderPipeline] = None


def get_s3_client() -> S3Client:
    """Provide a cached S3Client instance."""
    global _s3_client
    if _s3_client is None:
        try:
            _s3_client = S3Client()
        except S3ConfigError as e:
            logger.error(f"S3Client initialization failed: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"S3 client unavailable: {e}",
            ) from e
        except Exception as e:
            logger.error(f"Unexpected error initializing S3Client: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"S3 client unavailable: {e}",
            ) from e
    return _s3_client


def get_s3_status() -> bool:
    """Check if S3Client is available without raising HTTPException."""
    try:
        client = get_s3_client()
        return client is not None
    except Exception:
        return False


def get_storage_manager(
    s3_client: S3Client = Depends(get_s3_client),
) -> StorageManager:
    """Provide a cached StorageManager instance."""
    global _storage_manager
    if _storage_manager is None:
        _storage_manager = StorageManager(s3_client=s3_client)
    return _storage_manager


def get_tts_engine() -> Any:
    """Provide a platform-appropriate TTS engine instance."""
    global _tts_engine
    if _tts_engine is None:
        sys_plat = platform.system()
        try:
            if sys_plat == "Darwin":
                logger.info("Initializing macOS native TTS engine (AVFoundation)")
                _tts_engine = create_tts_engine(use_native_macos=True, language="en-US")
            else:
                logger.info("Initializing IndexTTS GPU inference engine")
                _tts_engine = create_tts_engine(
                    use_native_macos=False,
                    cfg_path="checkpoints/config.yaml",
                    model_dir="checkpoints",
                    is_fp16=True,
                    use_cuda_kernel=False,
                )
        except Exception as e:
            logger.error(f"Failed to initialize TTS engine: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"TTS engine unavailable: {e}",
            ) from e
    return _tts_engine


def get_cache_manager() -> CacheManager:
    """Provide a cached CacheManager instance."""
    global _cache_manager
    if _cache_manager is None:
        try:
            config = WorkerConfig.from_env()
            if config.cache_enabled:
                _cache_manager = CacheManager(
                    cache_dir=config.cache_dir,
                    max_entries=config.cache_max_entries,
                    eviction_threshold=config.cache_eviction_threshold,
                    max_size_mb=config.cache_max_size_mb,
                )
            else:
                _cache_manager = CacheManager(cache_dir=config.cache_dir)
        except Exception as e:
            logger.warning(f"Error loading WorkerConfig for cache: {e}, using default CacheManager")
            _cache_manager = CacheManager(cache_dir="outputs/cache")
    return _cache_manager


def get_tts_pipeline(
    tts_engine: Any = Depends(get_tts_engine),
    storage_manager: StorageManager = Depends(get_storage_manager),
    cache_manager: CacheManager = Depends(get_cache_manager),
) -> SynthesisPipeline:
    """Provide a cached SynthesisPipeline instance."""
    global _tts_pipeline
    if _tts_pipeline is None:
        try:
            config = WorkerConfig.from_env()
            _tts_pipeline = SynthesisPipeline(
                tts_engine=tts_engine,
                storage_manager=storage_manager,
                cache_manager=cache_manager,
                use_fast_inference=config.use_fast_inference,
                normalization_enabled=config.normalization_enabled,
                normalization_target_lufs=config.normalization_target_lufs,
            )
        except Exception as e:
            logger.error(f"Failed to initialize SynthesisPipeline: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"TTS synthesis pipeline unavailable: {e}",
            ) from e
    return _tts_pipeline


def get_vox_pipeline(
    s3_client: S3Client = Depends(get_s3_client),
) -> VoxRenderPipeline:
    """Provide a cached VoxRenderPipeline instance."""
    global _vox_pipeline
    if _vox_pipeline is None:
        try:
            config = WorkerConfig.from_env()
            ffmpeg_path = getattr(config, "vox_render_ffmpeg_path", "ffmpeg")
            _vox_pipeline = VoxRenderPipeline(
                s3_client=s3_client,
                ffmpeg_path=ffmpeg_path,
                local_tts_output_dir="outputs/tts_output",
            )
        except Exception as e:
            logger.error(f"Failed to initialize VoxRenderPipeline: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Vox render pipeline unavailable: {e}",
            ) from e
    return _vox_pipeline
