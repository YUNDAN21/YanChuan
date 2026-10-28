"""One translation consumer, bounded backlog, coalesced partials and versioned results."""

import logging
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

logger = logging.getLogger(__name__)


class Translator(Protocol):
    def translate(self, text: str, source_language: str, target_language: str) -> str: ...


@dataclass(frozen=True)
class TranslationUnit:
    key: str
    text: str
    start_seconds: float
    source_language: str = "auto"
    pending: bool = False


@dataclass(frozen=True)
class TranslationResult:
    key: str
    source_text: str
    text: str
    target_language: str
    start_seconds: float
    pending: bool
    state: str = "ready"
    error: str = ""
    generation: int = 0


class TranslationWorker:
    def __init__(
        self,
        translator: Translator,
        target_language="zh",
        *,
        on_update=None,
        queue_limit=32,
        debounce_seconds=0.7,
    ):
        if target_language not in {"zh", "ja", "en"} or queue_limit < 1:
            raise ValueError("翻译语言或队列预算无效")
        self.translator, self.target_language = translator, target_language
        self.on_update = on_update or (lambda result: None)
        self.queue_limit, self.debounce_seconds = queue_limit, debounce_seconds
        self._condition = threading.Condition(threading.RLock())
        self._queue = deque()
        self._partials = {}
        self._latest = {}
        self._cache = OrderedDict()
        self._closing = False
        self._generation = 0
        self._last_pending = False
        self._thread = threading.Thread(target=self._run, name="subtitle-translation", daemon=True)
        self._thread.start()

    def configure(self, translator, target_language):
        if target_language not in {"zh", "ja", "en"}:
            raise ValueError("不支持的翻译语言")
        with self._condition:
            self._generation += 1
            self.translator, self.target_language = translator, target_language
            self._queue.clear()
            self._partials.clear()
            self._latest.clear()
            self._cache.clear()
            self._condition.notify_all()

    def reset_pending(self, keys=()):
        with self._condition:
            for key in list(self._partials):
                if key not in keys:
                    self._partials.pop(key, None)
                    self._latest.pop(key, None)
            for key, value in list(self._latest.items()):
                if value[2] and key not in keys:
                    self._latest.pop(key, None)

    def submit(self, unit: TranslationUnit):
        if not unit.text.strip():
            return
        skipped = None
        with self._condition:
            if self._closing:
                return
            token = (self._generation, unit.text, unit.pending)
            if self._latest.get(unit.key) == token:
                return
            self._latest[unit.key] = token
            job = (unit, token, monotonic())
            if unit.pending:
                self._partials[unit.key] = job
            else:
                if len(self._queue) >= self.queue_limit:
                    dropped, _, _ = self._queue.popleft()
                    skipped = TranslationResult(
                        dropped.key,
                        dropped.text,
                        "",
                        self.target_language,
                        dropped.start_seconds,
                        False,
                        "skipped",
                        "翻译积压，已优先处理较新的字幕",
                        self._generation,
                    )
                self._queue.append(job)
            self._condition.notify_all()
        if skipped is not None:
            self._notify(skipped)

    def _notify(self, result):
        try:
            self.on_update(result)
        except Exception:  # noqa: BLE001 - translation observers do not stop the worker
            logger.error("Translation observer failed")

    def _take(self):
        ready = [
            job for job in self._partials.values() if monotonic() - job[2] >= self.debounce_seconds
        ]
        if ready and (not self._last_pending or not self._queue):
            job = ready[-1]
            self._partials.pop(job[0].key, None)
            self._last_pending = True
            return job
        if self._queue:
            self._last_pending = False
            return self._queue.popleft()
        return None

    def _run(self):
        while True:
            with self._condition:
                if self._closing:
                    return
                job = self._take()
                if job is None:
                    self._condition.wait(0.1)
                    continue
                unit, token, _ = job
                if self._latest.get(unit.key) != token:
                    continue
                translator, target = self.translator, self.target_language
                cache_key = (token[0], unit.source_language, unit.text)
                cached = self._cache.get(cache_key)
            error = ""
            try:
                text = (
                    cached
                    if cached is not None
                    else translator.translate(unit.text, unit.source_language, target)
                )
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("翻译服务返回空内容")
                text = text.strip()
            except Exception as exc:  # noqa: BLE001 - translation failure cannot stop ASR
                text, error = "", str(exc)
            with self._condition:
                if self._closing or token[0] != self._generation:
                    continue
                if not error:
                    self._cache[cache_key] = text
                    if len(self._cache) > 512:
                        self._cache.popitem(last=False)
                if self._latest.get(unit.key) != token:
                    continue
                result = TranslationResult(
                    unit.key,
                    unit.text,
                    text,
                    target,
                    unit.start_seconds,
                    unit.pending,
                    "error" if error else "ready",
                    error,
                    token[0],
                )
            self._notify(result)

    @property
    def generation(self):
        return self._generation

    def close(self, *, wait=True):
        with self._condition:
            self._closing = True
            self._queue.clear()
            self._partials.clear()
            self._condition.notify_all()
        if wait:
            self._thread.join()
