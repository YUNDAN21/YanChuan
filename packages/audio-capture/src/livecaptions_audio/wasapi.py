import logging
import sys
import threading
from collections import deque
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
        self._tracks: list[deque[CapturedBlock]] = []
        self._blocks_lock = threading.Lock()
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

    def _run(self):
        streams = []
        pa = None
        try:
            module = _pyaudio()
            pa = module.PyAudio()
            available = _devices(pa, module)
            tracks = self._tracks
            for kind, selected in [
                ("microphone", self.microphone_id),
                ("system", self.system_id),
            ]:
                if self.source not in {kind, "both"}:
                    continue
                candidates = [device for device in available if device["kind"] == kind]
                if selected is not None:
                    candidates = [device for device in candidates if device["id"] == selected]
                else:
                    candidates.sort(key=lambda device: not device["default"])
                if not candidates:
                    raise ValueError(f"找不到所选的 {kind} 设备，请刷新设备列表。")
                device = candidates[0]
                rate, channels = device["sample_rate"], min(2, device["channels"])
                blocks: deque[CapturedBlock] = deque()
                tracks.append(blocks)

                def callback(
                    data, frames, time_info, flags, *, rate=rate, channels=channels, blocks=blocks
                ):
                    if self.stop_event.is_set():
                        return None, module.paComplete
                    if data and self.started:
                        samples = (
                            np.frombuffer(data, dtype=np.float32).reshape(-1, channels).mean(axis=1)
                        )
                        offset = max(0, perf_counter() - self.started - len(samples) / rate)
                        if self.limit is None or offset < self.limit:
                            self._append_block(blocks, CapturedBlock(offset, rate, samples.copy()))
                    if flags and "设备录音缓冲曾溢出，可能存在少量丢帧。" not in self.warnings:
                        self.warnings.append("设备录音缓冲曾溢出，可能存在少量丢帧。")
                    return None, module.paContinue

                stream = pa.open(
                    format=module.paFloat32,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=int(device["id"]),
                    frames_per_buffer=1024,
                    stream_callback=callback,
                    start=False,
                )
                streams.append(stream)
            self.started = perf_counter()
            self.last_heartbeat = self.started
            for stream in streams:
                stream.start_stream()
            self.ready_event.set()
            while not self.stop_event.wait(0.25):
                elapsed = perf_counter() - self.started
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
            for stream in streams:
                stream.stop_stream()
            if self.archive:
                self._persist(self.duration)
        except Exception as exc:
            logger.exception("WASAPI capture failed")
            self.error = exc
        finally:
            for stream in streams:
                try:
                    stream.close()
                except OSError:
                    pass
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
        return mix_samples(tracks, end - start, start)

    def _append_block(self, blocks, block):
        with self._blocks_lock:
            blocks.append(block)
            cutoff = block.offset_seconds - self.buffer_seconds
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
