"""Single-consumer summary scheduling; network latency never blocks transcription."""

import logging
import math
import threading
from time import monotonic
from typing import Protocol

from .store import MeetingStore, TranscriptEntry

logger = logging.getLogger(__name__)


class SummaryClient(Protocol):
    model: str

    def summarize(self, title: str, entries: list[TranscriptEntry], previous_summary="") -> str: ...


class SummaryWorker:
    def __init__(
        self,
        store: MeetingStore,
        client: SummaryClient,
        *,
        interval_seconds=300,
        automatic=True,
        on_update=None,
        clock=monotonic,
        max_chars=12000,
    ):
        self.store, self.client = store, client
        self.clock, self.max_chars = clock, max_chars
        self.on_update = on_update or (lambda kind, value: None)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closing = False
        self._requested = 0
        self._inflight = 0
        self.busy = False
        self.last_error = ""
        self.configure(interval_seconds, automatic)
        self._thread = threading.Thread(target=self._run, name="meeting-summary", daemon=True)

    def configure(self, interval_seconds, automatic):
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("总结间隔必须为有限正数")
        with self._lock:
            self.interval_seconds, self.automatic = interval_seconds, automatic
            self.next_due = self.clock() + interval_seconds

    def start(self):
        self._thread.start()

    def set_client(self, client: SummaryClient):
        """Use a new client for the next task; the current task keeps its snapshot."""
        with self._lock:
            self.client = client

    @property
    def running(self):
        return self._thread.is_alive()

    def request(self):
        with self._lock:
            target = self.store.last_entry
            if target <= self.store.metadata["summary_cursor"]:
                return False
            if target <= self._inflight:
                return True
            self._requested = max(self._requested, target)
        self._wake.set()
        return True

    def tick(self):
        with self._lock:
            due = not self._closing and self.automatic and self.clock() >= self.next_due
            if due:
                self.next_due = self.clock() + self.interval_seconds
        if due:
            self.request()

    def close(self, *, drain=True):
        with self._lock:
            self._closing = True
        if drain:
            self.request()
        self._wake.set()

    def wait(self, timeout=None):
        self._thread.join(timeout)

    def _notify(self, kind, value):
        try:
            self.on_update(kind, value)
        except Exception:
            logger.exception("Meeting summary observer failed")

    def _run(self):
        while True:
            self._wake.wait(0.2)
            self._wake.clear()
            self.tick()
            with self._lock:
                target, self._requested = self._requested, 0
                self._inflight = target
                client = self.client
            if target > self.store.metadata["summary_cursor"]:
                self.busy = True
                self._notify("busy", True)
                try:
                    while batch := self.store.pending_batch(target, self.max_chars):
                        summaries = self.store.summaries()
                        text = client.summarize(
                            self.store.metadata["title"],
                            batch,
                            summaries[-1].text if summaries else "",
                        )
                        summary = self.store.save_summary(batch, text, client.model)
                        self.last_error = ""
                        self._notify("summary", summary)
                except Exception as exc:  # noqa: BLE001 - injected client errors must be redacted
                    # Do not log upstream exception bodies or credentials.
                    self.last_error = str(exc).replace(
                        getattr(getattr(client, "settings", None), "api_key", "\0"),
                        "[redacted]",
                    )
                    self.store.record_summary_error(self.last_error)
                    self._notify("error", self.last_error)
                finally:
                    self.busy = False
                    with self._lock:
                        self._inflight = 0
                    self._notify("busy", False)
            with self._lock:
                self._inflight = 0
                if self._closing and self._requested == 0:
                    return
