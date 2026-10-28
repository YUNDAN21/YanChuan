from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from livecaptions_asr.domain import ASRError

from .http_common import same_origin


class SessionReference(BaseModel):
    session_id: str
    language: str = "auto"


def job_router(jobs, captions):
    router = APIRouter(
        prefix="/v1/transcription-jobs", tags=["file jobs"], dependencies=[Depends(same_origin)]
    )

    @router.post("", status_code=202)
    async def submit(
        file: Annotated[UploadFile, File()],
        language: Annotated[str, Form()] = "auto",
        context: Annotated[str, Form(max_length=2000)] = "",
    ):
        try:
            return await run_in_threadpool(
                jobs.submit, file.file, file.filename or "audio.wav", language, context
            )
        finally:
            await file.close()

    @router.post("/from-session", status_code=202)
    async def from_session(config: SessionReference):
        session = captions.find(config.session_id)
        if not session.done.is_set():
            raise ASRError("audio_not_ready", "请先停止实时字幕并等待收尾。")
        source = session.audio
        if source is None:
            raise ASRError("audio_not_ready", "本次未保存完整录音。")
        return await run_in_threadpool(jobs.submit, source, "录音.wav", config.language)

    @router.get("/{job_id}")
    async def status(job_id: str):
        return jobs.find(job_id).snapshot()

    @router.post("/{job_id}/cancel")
    async def cancel(job_id: str):
        job = jobs.find(job_id)
        job.cancel.set()
        return job.snapshot()

    return router
