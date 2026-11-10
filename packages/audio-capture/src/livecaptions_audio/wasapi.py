import logging
import sys
import threading
from collections import deque
from concurrent.futures import Future
from queue import Empty, Queue
from time import perf_counter

import numpy as np

from livecaptions_asr.domain import ASRError, AudioData

from .mixer import CapturedBlock, mix_samples
from .protocols import AudioSink

logger = logging.getLogger(__name__)


def _pyaudio():
    if sys.platform != "win32":
        raise ASRError("capture_unavailable", "当前本地录音后端仅支持 Windows WASAPI。")
    import pyaudiowpatch

    return pyaudiowpatch


def _devices(pa, module) -> list[dict]:
    host = pa.get_host_api_info_by_type(module.paWASAPI)
    default_input = int(host.get("defaultInputDevice", -1))
    default_output = int(host.get("defaultOutputDevice", -1))
    output_name = pa.get_device_info_by_index(default_output)["name"] if default_output >= 0 else ""
    devices = []
    for index in range(pa.get_device_count()):
        item = pa.get_device_info_by_index(index)
        if item["hostApi"] != host["index"] or item["maxInputChannels"] < 1:
            continue
        loopback = bool(item.get("isLoopbackDevice", False))
        devices.append(
            {
                "id": str(item["index"]),
                "name": item["name"].replace(" [Loopback]", ""),
                "kind": "system" if loopback else "microphone",
                "default": (bool(output_name) and output_name in item["name"])
                if loopback
                else index == default_input,
                "sample_rate": int(item["defaultSampleRate"]),
                "channels": int(item["maxInputChannels"]),
            }
        )
    return devices


def list_devices() -> list[dict]:
    module = _pyaudio()
    try:
        with module.PyAudio() as pa:
            return _devices(pa, module)
    except (OSError, ValueError) as exc:
        raise ASRError("capture_unavailable", f"无法枚举 WASAPI 音频设备：{exc}") from exc


class WasapiRecording:
    """Own PortAudio initialization, streams, and teardown on a dedicated thread."""

    def __init__(
        self,
        source: str,
        microphone_id: str | None,
        system_id: str | None,
        limit: float | None,
        *,
        buffer_seconds=90,
        archive: AudioSink | None = None,
        idle_seconds=120,
    ):
        self._validate_source(source)
        self.source = source
        self.microphone_id = microphone_id
        self.system_id = system_id
        self.limit = limit
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.done_event = threading.Event()
        self.error: Exception | None = None
        self.started = 0.0
        self.duration = 0.0
        self.archive = archive
        self.buffer_seconds = buffer_seconds
        self.idle_seconds = idle_seconds
        self.last_heartbeat = perf_counter()
        self._written = 0.0
        self._persist_lock = threading.Lock()
        self.warnings: list[str] = []
        self._tracks: list[deque[CapturedBlock]] = [deque(), deque()]
        self._source_tracks = dict(zip(("microphone", "system"), self._tracks, strict=True))
        self._blocks_lock = threading.Lock()
        self._source_lock = threading.Lock()
        self._source_requests: Queue[tuple[str, Future[str]]] = Queue()
        self.thread = threading.Thread(target=self._run, name="wasapi-capture", daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready_event.wait(12):
            self.stop_event.set()
            raise ASRError("capture_unavailable", "音频设备启动超时，请检查设备权限或占用状态。")
        if self.error:
            raise ASRError("capture_unavailable", f"录音启动失败：{self.error}") from self.error

    def status(self):
        return {
            "state": "error"
            if self.error
            else "stopped"
            if self.done_event.is_set()
            else "recording",
            "source": self.source,
            "duration_seconds": round(
                self.duration
                if self.done_event.is_set()
                else max(0, perf_counter() - self.started),
                2,
            ),
            "limit_seconds": self.limit,
            "buffer_seconds": self.buffer_seconds,
            "buffered_blocks": sum(len(track) for track in self._tracks),
            "saved_audio": self.archive is not None,
        }

    def stop(self) -> AudioSink | None:
        self.stop_event.set()
        if not self.done_event.wait(12):
            raise ASRError("capture_unavailable", "音频设备结束超时。")
        if self.error:
            if isinstance(self.error, ASRError):
                raise self.error
            raise ASRError("capture_failed", f"录音失败：{self.error}") from self.error
        return self.archive

    @staticmethod
    def _validate_source(source):
        if source not in {"system", "microphone", "both"}:
            raise ValueError("音源必须为 system、microphone 或 both")

    def set_source(self, source: str) -> Future[str]:
        """Schedule a device change on its owner thread; time and stored PCM are retained."""
        self._validate_source(source)
        result: Future[str] = Future()
        with self._source_lock:
            if (
                not self.ready_event.is_set()
                or self.stop_event.is_set()
                or self.done_event.is_set()
            ):
                raise RuntimeError("当前没有正在采集的音频")
            self._source_requests.put((source, result))
        return result

    def _open_input(self, pa, module, available, kind):
        selected = self.microphone_id if kind == "microphone" else self.system_id
        candidates = [device for device in available if device["kind"] == kind]
        if selected is not None:
            candidates = [device for device in candidates if device["id"] == selected]
        else:
            candidates.sort(key=lambda device: not device["default"])
        if not candidates:
            label = "麦克风" if kind == "microphone" else "系统声音"
            raise ValueError(f"找不到所选的{label}设备，请检查设备连接和选择。")
        device = candidates[0]
        rate, channels = device["sample_rate"], min(2, device["channels"])
        # New streams stay muted until all requested inputs have started successfully.
        entry = {"stream": None, "since": None}
        blocks = self._source_tracks[kind]

        def callback(data, frames, time_info, flags):
            if self.stop_event.is_set():
                return None, module.paComplete
            if data and self.started:
                samples = np.frombuffer(data, dtype=np.float32).reshape(-1, channels).mean(axis=1)
                offset = max(0, perf_counter() - self.started - len(samples) / rate)
                with self._blocks_lock:
                    since = entry["since"]
                    if since is not None and (self.limit is None or offset < self.limit):
                        trim = max(0, int(np.ceil((since - offset) * rate)))
                        if trim < len(samples):
                            self._append_block_locked(
                                blocks,
                                CapturedBlock(offset + trim / rate, rate, samples[trim:].copy()),
                            )
            if flags and "设备录音缓冲曾溢出，可能存在少量丢帧。" not in self.warnings:
                self.warnings.append("设备录音缓冲曾溢出，可能存在少量丢帧。")
            return None, module.paContinue

        entry["stream"] = pa.open(
            format=module.paFloat32,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=int(device["id"]),
            frames_per_buffer=1024,
            stream_callback=callback,
            start=False,
        )
        return entry

    @staticmethod
    def _close_input(entry):
        stream = entry["stream"]
        try:
            if not entry.get("stopped", False):
                stream.stop_stream()
        except OSError:
            logger.exception("Failed to stop audio input")
        finally:
            try:
                stream.close()
            except OSError:
                logger.exception("Failed to close audio input")

    def _switch_inputs(self, pa, module, streams, source):
        wanted = {"microphone", "system"} if source == "both" else {source}
        added = {}
        try:
            available = _devices(pa, module)
            for kind in ("microphone", "system"):
                if kind in wanted and kind not in streams:
                    added[kind] = self._open_input(pa, module, available, kind)
                    added[kind]["stream"].start_stream()
            if self.stop_event.is_set():
                raise RuntimeError("录音正在结束")
        except Exception:
            for entry in added.values():
                self._close_input(entry)
            raise
        with self._blocks_lock:
            boundary = perf_counter() - self.started
            for entry in added.values():
                entry["since"] = boundary
            removed = [streams.pop(kind) for kind in list(streams) if kind not in wanted]
            for entry in removed:
                entry["since"] = None
            streams.update(added)
            self.source = source
        for entry in removed:
            self._close_input(entry)

    def _change_requested_sources(self, pa, module, streams):
        while not self.stop_event.is_set():
            try:
                source, result = self._source_requests.get_nowait()
            except Empty:
                return
            if not result.set_running_or_notify_cancel():
                continue
            try:
                if source != self.source:
                    self._switch_inputs(pa, module, streams, source)
            except Exception as exc:  # noqa: BLE001 - failed replacement keeps the current inputs
                result.set_exception(ASRError("capture_source_failed", f"切换音源失败：{exc}"))
            else:
                result.set_result(self.source)

    def _run(self):
        streams = {}
        pa = None
        try:
            module = _pyaudio()
            pa = module.PyAudio()
            available = _devices(pa, module)
            for kind in ("microphone", "system"):
                if self.source not in {kind, "both"}:
                    continue
                streams[kind] = self._open_input(pa, module, available, kind)
            self.started = perf_counter()
            self.last_heartbeat = self.started
            for entry in streams.values():
                entry["since"] = 0.0
                entry["stream"].start_stream()
            self.ready_event.set()
            while not self.stop_event.wait(0.05):
                self._change_requested_sources(pa, module, streams)
                elapsed = perf_counter() - self.started
                with self._blocks_lock:
                    self._prune_locked(elapsed)
                if self.archive:
                    self._persist(max(0, int((elapsed - 0.2) * 10) / 10))
                if self.limit is not None and elapsed >= self.limit:
                    break
                if perf_counter() - self.last_heartbeat > self.idle_seconds:
                    self.warnings.append("客户端失联，录音已自动停止。")
                    break
            self.duration = perf_counter() - self.started
            if self.limit is not None:
                self.duration = min(self.limit, self.duration)
            self.stop_event.set()
            for entry in streams.values():
                entry["stream"].stop_stream()
                entry["stopped"] = True
            if self.archive:
                self._persist(self.duration)
        except Exception as exc:
            logger.exception("WASAPI capture failed")
            self.error = exc
        finally:
            with self._source_lock:
                self.stop_event.set()
                pending = []
                while not self._source_requests.empty():
                    pending.append(self._source_requests.get_nowait()[1])
            for result in pending:
                if result.set_running_or_notify_cancel():
                    result.set_exception(RuntimeError("录音已结束"))
            for entry in streams.values():
                self._close_input(entry)
            if pa is not None:
                try:
                    pa.terminate()
                except OSError:
                    logger.exception("Failed to terminate PortAudio")
            if self.archive:
                try:
                    self.archive.finish()
                except OSError as exc:
                    self.error = self.error or exc
                    logger.exception("Failed to finish audio archive")
            self.ready_event.set()
            self.done_event.set()

    def window(self, start: float, end: float) -> AudioData:
        duration = (
            self.duration if self.done_event.is_set() else max(0, perf_counter() - self.started)
        )
        earliest = max(0, duration - self.buffer_seconds + 0.1)
        if start < earliest:
            if self.archive and end <= self.archive.duration + 0.01:
                return AudioData.from_pcm16k(
                    self.archive.samples(start, min(end, self.archive.duration))
                )
            raise ASRError("capture_backlog", "识别落后超出音频缓冲；请启用保存录音或降低负载。")
        return AudioData.from_pcm16k(self._samples(start, end))

    def _samples(self, start: float, end: float) -> np.ndarray:
        with self._blocks_lock:
            tracks = [
                [
                    block
                    for block in track
                    if block.offset_seconds < end
                    and block.offset_seconds + len(block.samples) / block.sample_rate > start
                ]
                for track in self._tracks
            ]
        return mix_samples(tracks, end - start, start, active_gain=True)

    def _append_block(self, blocks, block):
        with self._blocks_lock:
            self._append_block_locked(blocks, block)

    def _append_block_locked(self, blocks, block):
        blocks.append(block)
        self._prune_locked(block.offset_seconds)

    def _prune_locked(self, elapsed):
        cutoff = elapsed - self.buffer_seconds
        for blocks in self._tracks:
            while (
                blocks
                and blocks[0].offset_seconds + len(blocks[0].samples) / blocks[0].sample_rate
                < cutoff
            ):
                blocks.popleft()

    def heartbeat(self):
        self.last_heartbeat = perf_counter()

    def flush_to(self, end: float):
        self._persist(end)

    def _persist(self, end: float):
        with self._persist_lock:
            if end - self._written > self.buffer_seconds - 1:
                raise ASRError(
                    "storage_failed", "磁盘归档落后超出缓冲，已停止采集以避免静默丢失录音。"
                )
            while self._written < end - 0.00001:
                stop = min(end, self._written + 1)
                try:
                    self.archive.append(self._samples(self._written, stop))
                except OSError as exc:
                    raise ASRError("storage_failed", f"录音写入磁盘失败：{exc}") from exc
                self._written = stop
