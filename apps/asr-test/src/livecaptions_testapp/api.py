import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from livecaptions_asr.domain import ASRError, RecognitionEngine

from .bootstrap import build_recognizer
from .caption_api import caption_router
from .captions.service import CaptionService
from .capture.service import RecordingService
from .config import Settings
from .http_common import same_origin
from .job_api import job_router
from .jobs import TranscriptionJobs

ERROR_STATUS = {
    "upload_too_large": 413,
    "audio_too_long": 413,
    "server_busy": 503,
    "vulkan_unavailable": 503,
    "native_library_missing": 503,
    "native_library_invalid": 503,
    "native_abi_mismatch": 503,
    "gpu_fallback_rejected": 503,
    "invalid_model": 422,
    "model_missing": 503,
    "model_not_found": 503,
    "model_not_ready": 503,
    "model_load_failed": 503,
    "inference_failed": 503,
    "out_of_memory": 503,
    "capture_unavailable": 503,
    "capture_failed": 503,
    "recording_busy": 409,
    "recording_not_found": 404,
    "audio_not_ready": 409,
    "job_not_found": 404,
    "capture_backlog": 503,
    "storage_failed": 507,
}


def create_app(
    settings: Settings | None = None,
    engine: RecognitionEngine | None = None,
    recording_service: RecordingService | None = None,
    caption_service: CaptionService | None = None,
) -> FastAPI:
    config = settings or Settings()
    service = build_recognizer(config, engine)
    recordings = recording_service or RecordingService(
        config.recording_limit_seconds,
        archive_dir=config.data_dir / "recordings",
        buffer_seconds=config.capture_buffer_seconds,
        archive_chunk_seconds=config.archive_chunk_seconds,
        idle_seconds=config.capture_idle_seconds,
    )
    captions = caption_service or CaptionService(service, recordings, config)
    jobs = TranscriptionJobs(service, config.data_dir / "uploads", config.max_upload_bytes)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await run_in_threadpool(captions.close)
        await run_in_threadpool(jobs.close)
        await run_in_threadpool(recordings.close)
        await run_in_threadpool(service.close)

    app = FastAPI(
        title="LiveCaptions ASR",
        version="0.1.0",
        description="本地 Qwen3-ASR 文件转写。当前适配器不支持原生流式。",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.captions = captions
    app.state.jobs = jobs
    app.include_router(caption_router(captions))
    app.include_router(job_router(jobs, captions))

    @app.exception_handler(ASRError)
    async def handle_asr_error(request, exc: ASRError):
        return JSONResponse(
            status_code=ERROR_STATUS.get(exc.code, 422),
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/", include_in_schema=False)
    async def root():
        return {"service": "LiveCaptions ASR", "docs": "/docs"}

    @app.get("/health")
    async def health():
        status = service.engine.status()
        return {"status": "ok", "model_state": status.state, "ready": status.state == "ready"}

    @app.get("/v1/models")
    async def models():
        return {"data": [asdict(service.status())]}

    @app.get("/v1/audio/devices", dependencies=[Depends(same_origin)])
    async def devices():
        return {"devices": await run_in_threadpool(recordings.devices)}

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc: Exception):
        logging.getLogger(__name__).exception("Unhandled request error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "内部错误，请查看服务日志。"}},
        )

    return app
