import logging
import threading
from contextlib import closing
from time import perf_counter
from uuid import uuid4

from livecaptions_asr.domain import ASRError, normalize_language
from livecaptions_asr.recognizer import Recognizer
from livecaptions_asr.stream import StreamOptions

from ..archive import AudioArchive
from ..capture.service import RecordingService
from ..config import Settings
from ..endpoints import SegmenterFactory

logger = logging.getLogger(__name__)


class CaptionSession:
    def __init__(
        self,
        asr,
        recordings,
        config,
        source,
        microphone_id,
        system_id,
        language,
        save_audio=False,
        *,
        segmenter_factory=None,
    ):
        self.id = uuid4().hex
        self.asr = asr
        self.recordings = recordings
        self.config = config
        self.segmenter_factory = segmenter_factory or SegmenterFactory(config)
        self.capture_config = (source, microphone_id, system_id)
        self.language = language
        self.state = "preparing"
        self.revision = 0
        self.segments = {}
        self.duration = 0.0
        self.processed = 0.0
        self.first_text_seconds = None
        self.error = None
        self.audio: AudioArchive | None = None
        self.save_audio = save_audio
        self.sections = []
        self.buffered_blocks = 0
        self.warnings = []
        self.lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.cancel_requested = threading.Event()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="caption-session", daemon=True)
        self.capture_started = 0.0
        self.capture_id = None

    def snapshot(self):
        with self.lock:
            segments = [dict(value) for _, value in sorted(self.segments.items())]
            duration = self.duration
            if self.state == "recording":
                duration = max(duration, perf_counter() - self.capture_started)
                if self.recordings.max_seconds is not None:
                    duration = min(self.recordings.max_seconds, duration)
            return {
                "id": self.id,
                "state": self.state,
                "mode": "chunked",
                "native_streaming": False,
                "segmentation": self.config.live_segmentation,
                "revision": self.revision,
                "segments": segments,
                "text": "\n".join(item["text"] for item in segments if item["text"]),
                "duration_seconds": round(duration, 2),
                "processed_seconds": round(self.processed, 2),
                "backlog_seconds": round(max(0, duration - self.processed), 2),
                "first_text_seconds": self.first_text_seconds,
                "limit_seconds": self.recordings.max_seconds,
                "error": self.error,
                "warnings": list(self.warnings),
                "audio_available": self.audio is not None,
                "save_audio": self.save_audio,
                "semantic_sections": list(self.sections),
                "archive_directory": str(self.audio.directory.resolve())
                if self.audio is not None
                else None,
                "finished": self.done.is_set(),
                "capture_buffer_seconds": self.recordings.buffer_seconds,
                "buffered_blocks": self.buffered_blocks,
            }

    def _update(self, **changes):
        with self.lock:
            for name, value in changes.items():
                setattr(self, name, value)

    def _events(self, capture_id, events):
        """Adapt library events to UI snapshots and the optional archive sink."""
        with closing(events):
            for event in events:
                self._event(capture_id, event)

    def _event(self, capture_id, event):
        if event.sentence is not None:
            group = event.sentence.to_dict()
            archive = self.recordings.archive(capture_id, group["end_seconds"])
            if archive:
                archive.section(group)
            with self.lock:
                self.sections.append(group)
                self.sections = self.sections[-300:]
        elif event.segment is not None:
            segment = event.segment.to_dict()
            segment["emitted_seconds"] = round(perf_counter() - self.capture_started, 3)
            with self.lock:
                self.revision = event.segment.revision
                if segment["text"] and self.first_text_seconds is None:
                    self.first_text_seconds = segment["emitted_seconds"]
                self.segments[segment["id"]] = segment
                while len(self.segments) > 300:
                    self.segments.pop(min(self.segments))

    def _run(self):
        capture_id = None
        stream = None
        segmenter = None
        try:
            segmenter = self.segmenter_factory()
            if self.stop_requested.is_set():
                return
            self.asr.prepare()
            if self.stop_requested.is_set():
                return
            capture = self.recordings.start(*self.capture_config, save_audio=self.save_audio)
            capture_id = capture["id"]
            self.capture_id = capture_id
            self.capture_started = perf_counter()
            self._update(state="recording")
            self.ready.set()
            stream = self.asr.create_stream(
                self.language,
                segmenter=segmenter,
                options=StreamOptions(
                    threshold=self.config.live_energy_threshold,
                    silence_seconds=self.config.live_silence_seconds,
                    max_segment_seconds=self.config.live_segment_seconds,
                    update_seconds=self.config.live_update_seconds,
                    semantic_seconds=self.config.semantic_max_seconds,
                ),
            )
            cursor = 0.0
            finished = False
            while True:
                if self.stop_requested.is_set() and not finished:
                    self._update(state="draining")
                    self.recordings.finish(capture_id)
                    finished = True
                status = self.recordings.status(capture_id)
                finished = finished or status["state"] in {"stopped", "error"}
                self._update(
                    duration=status["duration_seconds"],
                    buffered_blocks=status.get("buffered_blocks", 0),
                )
                if status["state"] == "error":
                    self.recordings.finish(capture_id)
                    raise ASRError("capture_failed", "录音设备发生错误。")
                if self.cancel_requested.is_set():
                    break
                # Safety margin for callbacks still appending captured frames.
                end = status["duration_seconds"] - (0 if finished else 0.15)
                end = min(max(0, int(end * 10) / 10), cursor + 1)
                if end - cursor >= 0.1 - 1e-6:
                    samples = self.recordings.window(capture_id, cursor, end).samples
                    self._events(
                        capture_id,
                        stream.feed(
                            samples,
                            emit_partial=not finished and status["duration_seconds"] - end < 2,
                        ),
                    )
                    cursor = end
                    self._update(processed=cursor)
                if finished and status["duration_seconds"] - cursor < 0.1:
                    final_end = status["duration_seconds"]
                    if final_end - cursor > 0.01:
                        samples = self.recordings.window(capture_id, cursor, final_end).samples
                        self._events(capture_id, stream.feed(samples, emit_partial=False))
                    self._update(processed=final_end)
                    break
                if status["duration_seconds"] - cursor > 1.5:
                    continue
                self.stop_requested.wait(0.15)
            if self.cancel_requested.is_set():
                stream.cancel()
            else:
                self._events(capture_id, stream.finish())
        except Exception as exc:
            logger.exception("Caption session failed")
            self._update(
                state="error",
                error={"code": getattr(exc, "code", "caption_failed"), "message": str(exc)},
            )
        finally:
            if stream is None and (close := getattr(segmenter, "close", None)):
                close()
            if stream is not None and not stream.closed:
                stream.cancel()
            if capture_id:
                try:
                    audio, warnings = self.recordings.stop(capture_id)
                    self._update(audio=audio, warnings=warnings)
                except Exception as exc:
                    logger.exception("Failed to finish caption recording")
                    self._update(
                        state="error", error={"code": "capture_failed", "message": str(exc)}
                    )
            with self.lock:
                if self.state != "error":
                    self.state = "stopped"
            self.ready.set()
            self.done.set()


class CaptionService:
    def __init__(
        self,
        asr: Recognizer,
        recordings: RecordingService,
        config: Settings,
        *,
        segmenter_factory=None,
    ):
        self.asr, self.recordings, self.config = asr, recordings, config
        self._session: CaptionSession | None = None
        self._lock = threading.Lock()
        self.segmenter_factory = segmenter_factory or SegmenterFactory(config)

    def start(self, source, microphone_id, system_id, language="auto", save_audio=False):
        normalize_language(language)
        with self._lock:
            if self._session and self._session.thread.is_alive():
                raise ASRError("recording_busy", "已有实时字幕会话，请先停止。")
            if self.recordings.active():
                raise ASRError("recording_busy", "已有音频采集，请先结束会话。")
            session = CaptionSession(
                self.asr,
                self.recordings,
                self.config,
                source,
                microphone_id,
                system_id,
                language,
                save_audio,
                segmenter_factory=self.segmenter_factory,
            )
            self._session = session
            try:
                session.thread.start()
            except RuntimeError:
                self._session = None
                raise
            return session.snapshot()

    def find(self, session_id, heartbeat=False):
        with self._lock:
            if not self._session or self._session.id != session_id:
                raise ASRError("recording_not_found", "字幕会话不存在或已被新会话替换。")
            session = self._session
        if heartbeat and session.capture_id:
            self.recordings.heartbeat(session.capture_id)
        return session

    def stop(self, session_id, cancel=False):
        session = self.find(session_id)
        if cancel:
            session.cancel_requested.set()
        session.stop_requested.set()
        return session.snapshot()

    def close(self):
        with self._lock:
            session = self._session
        if session:
            self.stop(session.id, cancel=True)
            session.thread.join(timeout=30)
