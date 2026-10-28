import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import soundfile as sf

from livecaptions_asr.domain import ASRError, normalize_language
from livecaptions_asr.results import FileProgress

from .archive import AudioArchive

logger = logging.getLogger(__name__)


class FileJob:
    def __init__(self, path, name, language, context):
        self.id = uuid4().hex
        self.path, self.name, self.language, self.context = path, name, language, context
        self.state = "queued"
        self.total_seconds = 0.0
        self.processed_seconds = 0.0
        self.chunks_completed = 0
        self.segments = []
        self.result = None
        self.error = None
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return {
                "id": self.id,
                "state": self.state,
                "filename": self.name,
                "total_seconds": self.total_seconds,
                "processed_seconds": self.processed_seconds,
                "chunks_completed": self.chunks_completed,
                "segments": list(self.segments[-300:]),
                "result": self.result,
                "error": self.error,
            }

    def progress(self, value: FileProgress):
        with self.lock:
            self.processed_seconds = value.processed_seconds
            self.chunks_completed = value.chunks_completed
            self.segments.append(value.segment.to_dict())


class TranscriptionJobs:
    """Two admitted file jobs; uploads stay on disk and are removed on every exit."""

    def __init__(self, asr, directory: Path, max_bytes=None):
        self.asr, self.directory, self.max_bytes = asr, directory, max_bytes
        # One file worker leaves an ASR admission slot available to live captions.
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-transcription")
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        self.jobs = {}

    def submit(self, source, name, language="auto", context=""):
        normalize_language(language)
        if not self.slots.acquire(blocking=False):
            raise ASRError("server_busy", "长文件任务已满，请等待或取消现有任务。")
        path = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{uuid4().hex}.audio"
            if isinstance(source, AudioArchive):
                blocks = source.iter_wav()
            else:
                source.seek(0)
                blocks = iter(lambda: source.read(1024 * 1024), b"")
            size = 0
            with path.open("wb") as target:
                for block in blocks:
                    size += len(block)
                    if self.max_bytes is not None and size > self.max_bytes:
                        raise ASRError("upload_too_large", "音频文件超过配置的大小限制。")
                    target.write(block)
            job = FileJob(path, name, language, context)
            with self.lock:
                # Completed results do not grow without bound across uploads.
                completed = [key for key, value in self.jobs.items() if value.done.is_set()]
                for key in completed[:-3]:
                    del self.jobs[key]
                self.jobs[job.id] = job
            self.pool.submit(self._run, job)
            return job.snapshot()
        except Exception as exc:
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Could not remove rejected upload")
            self.slots.release()
            if isinstance(exc, OSError):
                raise ASRError("storage_failed", f"上传临时文件写入失败：{exc}") from exc
            raise

    def _run(self, job):
        try:
            with job.lock:
                job.state = "running"
            if job.path.stat().st_size == 0:
                raise ASRError("empty_audio", "音频文件为空。")
            try:
                duration = sf.info(job.path).duration
            except (RuntimeError, ValueError, sf.LibsndfileError) as exc:
                raise ASRError("invalid_audio", "无法读取音频文件。") from exc
            with job.lock:
                job.total_seconds = duration
            result = self.asr.transcribe_file(
                job.path, job.language, job.context, on_segment=job.progress, cancel=job.cancel
            )
            with job.lock:
                job.result = result.to_dict()
                job.state = "cancelled" if job.cancel.is_set() else "completed"
        except ASRError as exc:
            with job.lock:
                job.state = "cancelled" if exc.code == "cancelled" else "error"
                job.error = (
                    None if job.state == "cancelled" else {"code": exc.code, "message": exc.message}
                )
        except Exception as exc:
            logger.exception("File job failed")
            with job.lock:
                job.state = "error"
                job.error = {"code": "transcription_failed", "message": str(exc)}
        finally:
            try:
                job.path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove temporary upload %s", job.path)
            self.slots.release()
            job.done.set()

    def find(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ASRError("job_not_found", "转写任务不存在或已被后续任务替换。")
            return self.jobs[job_id]

    def close(self):
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel.set()
        self.pool.shutdown(wait=True)
