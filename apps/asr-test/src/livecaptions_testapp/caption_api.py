from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from livecaptions_asr.domain import ASRError

from .archive_response import archive_response
from .captions.service import CaptionService
from .http_common import same_origin


class StartCaptions(BaseModel):
    source: Literal["microphone", "system", "both"] = "microphone"
    microphone_id: str | None = None
    system_id: str | None = None
    language: str = "auto"
    save_audio: bool = False


def caption_router(service: CaptionService):
    router = APIRouter(
        prefix="/v1/captions", tags=["captions"], dependencies=[Depends(same_origin)]
    )

    @router.post("")
    async def start(config: StartCaptions):
        return await run_in_threadpool(
            service.start,
            config.source,
            config.microphone_id,
            config.system_id,
            config.language,
            config.save_audio,
        )

    @router.get("/{session_id}")
    async def status(session_id: str):
        return await run_in_threadpool(lambda: service.find(session_id, heartbeat=True).snapshot())

    @router.post("/{session_id}/stop")
    async def stop(session_id: str):
        return await run_in_threadpool(service.stop, session_id)

    @router.post("/{session_id}/cancel")
    async def cancel(session_id: str):
        return await run_in_threadpool(service.stop, session_id, cancel=True)

    @router.get("/{session_id}/audio")
    async def audio(session_id: str, request: Request):
        session = service.find(session_id)
        if not session.done.is_set() or session.audio is None:
            raise ASRError("audio_not_ready", "录音尚未就绪。")
        return archive_response(session.audio, request)

    @router.get("/{session_id}/manifest")
    async def manifest(session_id: str):
        session = service.find(session_id)
        if session.audio is None:
            raise ASRError("audio_not_ready", "本次未保存录音，或归档尚未完成。")
        return FileResponse(
            session.audio.directory / "manifest.json", media_type="application/json"
        )

    return router
