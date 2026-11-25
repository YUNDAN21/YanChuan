"""Cancellable UTF-8 CLI pipes, independent of the provider's wire format."""

import logging
import os
import subprocess
from contextlib import contextmanager
from queue import Empty, Full, Queue
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic

from .errors import SummaryError

logger = logging.getLogger(__name__)
MAX_LINE_BYTES = 16 * 1024 * 1024


class AgentProcessError(SummaryError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class AgentProcess:
    def __init__(self, process, directory, label, timeout, cancelled):
        self.process, self.directory, self.label = process, directory, label
        self.cancelled = cancelled
        self.deadline = monotonic() + timeout
        self.input = Queue()
        self.output = Queue(maxsize=16)
        self.closed = Event()
        self.input_closed = False
        self.stderr_tail = b""
        self.stderr_bytes = 0
        self.stdout_lines = 0
        self.threads = []

    def start(self):
        streams = (
            (self._write_input, self.process.stdin),
            (self._read_output, self.process.stdout),
            (self._read_errors, self.process.stderr),
        )
        for read, pipe in streams:
            thread = Thread(target=read, name="meeting-agent-pipe", daemon=True)
            self.threads.append((thread, pipe))
            thread.start()

    def send(self, payload):
        self.input.put(payload)

    def close_input(self):
        if not self.input_closed:
            self.input_closed = True
            self.input.put(None)

    def _write_input(self):
        try:
            with self.process.stdin as pipe:
                while (payload := self.input.get()) is not None and not self.closed.is_set():
                    pipe.write(payload)
                    pipe.flush()
        except OSError:
            # A CLI can exit early after reporting a useful error on stdout or stderr.
            pass

    def _enqueue_output(self, item):
        while not self.closed.is_set():
            try:
                self.output.put(item, timeout=0.1)
                return
            except Full:
                continue

    def _read_output(self):
        try:
            with self.process.stdout as pipe:
                while line := pipe.readline(MAX_LINE_BYTES + 1):
                    if len(line) > MAX_LINE_BYTES:
                        raise AgentProcessError("output_limit", f"{self.label} 返回的事件过大")
                    self._enqueue_output(line)
        except (OSError, AgentProcessError) as exc:
            self._enqueue_output(exc)
        finally:
            self._enqueue_output(None)

    def _read_errors(self):
        try:
            with self.process.stderr as pipe:
                while block := pipe.read1(8192):
                    self.stderr_bytes += len(block)
                    self.stderr_tail = (self.stderr_tail + block)[-32768:]
        except OSError:
            pass

    def remaining(self):
        if self.cancelled is not None and self.cancelled.is_set():
            raise InterruptedError()
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise AgentProcessError("timeout", f"{self.label} 等待超时，请重试")
        return remaining

    def read_line(self):
        while True:
            try:
                line = self.output.get(timeout=min(self.remaining(), 0.1))
            except Empty:
                continue
            if isinstance(line, AgentProcessError):
                raise line
            if isinstance(line, OSError):
                raise AgentProcessError("pipe", f"{self.label} 通信失败，请重试") from None
            if line is not None:
                self.stdout_lines += 1
            return line

    def lines(self):
        while (line := self.read_line()) is not None:
            yield line

    def wait(self):
        while True:
            try:
                return self.process.wait(timeout=min(self.remaining(), 0.1))
            except subprocess.TimeoutExpired:
                continue

    def close(self, *, graceful):
        self.closed.set()
        self.close_input()
        if graceful and self.process.poll() is None:
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        for thread, pipe in self.threads:
            if thread.ident is not None:
                thread.join(timeout=0.5)
            if not thread.is_alive():
                pipe.close()
        owned = {pipe for _, pipe in self.threads}
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe not in owned:
                pipe.close()


@contextmanager
def agent_process(command, label, *, timeout, cancelled=None):
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError()
    startup = {}
    if os.name == "nt":
        # Keep Windows from allocating a console host for a pipe-only CLI.
        startup = {
            "creationflags": subprocess.DETACHED_PROCESS,
            "startupinfo": subprocess.STARTUPINFO(
                dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=subprocess.SW_HIDE
            ),
        }
    with TemporaryDirectory(prefix="yanchuan-agent-", ignore_cleanup_errors=True) as directory:
        try:
            process = subprocess.Popen(
                command,
                cwd=directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **startup,
            )
        except (OSError, ValueError) as exc:
            logger.warning("agent executable launch failed errno=%s", getattr(exc, "errno", None))
            raise AgentProcessError("launch", f"无法启动 {label}，请检查命令行程序路径") from None
        transport = AgentProcess(process, directory, label, timeout, cancelled)
        graceful = False
        try:
            transport.start()
            yield transport
            graceful = True
        finally:
            transport.close(graceful=graceful)
