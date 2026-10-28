import threading
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from livecaptions_audio.wasapi import WasapiRecording, list_devices

from livecaptions_asr.domain import ASRError, AudioData

from ..archive import AudioArchive


class Capture(Protocol):
    archive: AudioArchive | None
    warnings: list[str]

    def start(self) -> None: ...
    def stop(self) -> AudioArchive | None: ...
    def status(self) -> dict: ...
    def window(self, start: float, end: float) -> AudioData: ...
    def heartbeat(self) -> None: ...
    def flush_to(self, end: float) -> None: ...


class RecordingService:
    """Own a single caption capture and its bounded audio/optional disk archive."""

    def __init__(
        self,
        max_seconds=None,
        factory=WasapiRecording,
        enumerate_devices=list_devices,
        *,
        archive_dir=Path("data/recordings"),
        buffer_seconds=90,
        archive_chunk_seconds=30,
        idle_seconds=120,
    ):
        self.max_seconds, self.factory = max_seconds, factory
        self.enumerate_devices, self.archive_dir = enumerate_devices, archive_dir
        self.buffer_seconds, self.archive_chunk_seconds = buffer_seconds, archive_chunk_seconds
        self.idle_seconds = idle_seconds
        self._lock = threading.Lock()
        self._session: Capture | None = None
        self._session_id = None

    def devices(self):
        return self.enumerate_devices()

    def active(self):
        with self._lock:
            return self._session is not None

    def start(self, source, microphone_id, system_id, *, save_audio=False):
        with self._lock:
            if self._session is not None:
                raise ASRError("recording_busy", "已有字幕采集，请先结束会话。")
            session_id = uuid4().hex
            archive = (
                AudioArchive(self.archive_dir / session_id, self.archive_chunk_seconds)
                if save_audio
                else None
            )
            try:
                session = self.factory(
                    source,
                    microphone_id,
                    system_id,
                    self.max_seconds,
                    buffer_seconds=self.buffer_seconds,
                    archive=archive,
                    idle_seconds=self.idle_seconds,
                )
                session.start()
            except Exception:
                if archive and not archive.closed:
                    archive.finish()
                raise
            self._session, self._session_id = session, session_id
            return {"id": session_id, **session.status()}

    def _find(self, session_id) -> Capture:
        if self._session is None or session_id != self._session_id:
            raise ASRError("recording_not_found", "采集会话不存在或已经结束。")
        return self._session

    def status(self, session_id):
        with self._lock:
            return {"id": session_id, **self._find(session_id).status()}

    def heartbeat(self, session_id):
        with self._lock:
            if self._session_id == session_id:
                self._find(session_id).heartbeat()

    def window(self, session_id, start, end):
        with self._lock:
            session = self._find(session_id)
            if start < 0 or end <= start or end > session.status()["duration_seconds"] + 0.02:
                raise ASRError("invalid_audio_window", "录音窗口超出已采集音频。")
            return session.window(start, end)

    def archive(self, session_id, through=None):
        with self._lock:
            session = self._find(session_id)
            archive = session.archive
            if (
                archive
                and through is not None
                and not archive.closed
                and archive.duration < through
            ):
                session.flush_to(through)
            return archive

    def finish(self, session_id):
        """Stop hardware while retaining the short buffer for tail recognition."""
        with self._lock:
            return self._find(session_id).stop()

    def stop(self, session_id):
        with self._lock:
            session = self._find(session_id)
            try:
                return session.stop(), session.warnings
            finally:
                self._session = self._session_id = None

    def close(self):
        with self._lock:
            if self._session:
                try:
                    self._session.stop()
                finally:
                    self._session = self._session_id = None
