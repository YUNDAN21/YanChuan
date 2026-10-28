import threading
import time

from livecaptions_translation import TranslationUnit, TranslationWorker


def wait(predicate):
    end = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < end
        time.sleep(0.01)


class Translator:
    def __init__(self):
        self.calls = []

    def translate(self, text, source, target):
        self.calls.append((text, source, target))
        return f"{target}:{text}"


def test_partial_coalescing_cache_and_confirmation_reuse():
    translator, results = Translator(), []
    worker = TranslationWorker(translator, on_update=results.append, debounce_seconds=0.1)
    try:
        worker.submit(TranslationUnit("pending", "old", 0, pending=True))
        worker.submit(TranslationUnit("pending", "new", 0, pending=True))
        wait(lambda: len(results) == 1)
        assert results[0].source_text == "new"
        worker.reset_pending()
        worker.submit(TranslationUnit("final", "new", 0))
        wait(lambda: len(results) == 2)
        assert len(translator.calls) == 1
        assert results[1].key == "final" and not results[1].pending
    finally:
        worker.close()


def test_stale_inflight_revision_and_language_switch_never_publish():
    entered, release = threading.Event(), threading.Event()
    results = []

    class Slow(Translator):
        def translate(self, text, source, target):
            entered.set()
            release.wait(3)
            return "old result"

    worker = TranslationWorker(Slow(), on_update=results.append, debounce_seconds=0)
    try:
        worker.submit(TranslationUnit("a", "old", 0))
        assert entered.wait(1)
        worker.configure(Translator(), "ja")
        worker.submit(TranslationUnit("a", "new", 0))
        release.set()
        wait(lambda: len(results) == 1)
        assert results[0].target_language == "ja"
        assert results[0].text == "ja:new"
        assert results[0].generation == worker.generation
    finally:
        release.set()
        worker.close()


def test_failure_then_next_subtitle_succeeds():
    class Failed(Translator):
        def translate(self, text, source, target):
            if text == "bad":
                raise ValueError("service error")
            return "good"

    results = []
    worker = TranslationWorker(Failed(), on_update=results.append)
    try:
        worker.submit(TranslationUnit("a", "bad", 0))
        worker.submit(TranslationUnit("b", "ok", 1))
        wait(lambda: len(results) == 2)
        assert [r.state for r in results] == ["error", "ready"]
    finally:
        worker.close()


def test_bounded_backlog_prioritizes_new_content_and_keeps_source_keys():
    entered, release = threading.Event(), threading.Event()

    class Slow(Translator):
        def translate(self, text, source, target):
            entered.set()
            release.wait(3)
            return "ok"

    results = []
    worker = TranslationWorker(Slow(), queue_limit=2, on_update=results.append)
    try:
        worker.submit(TranslationUnit("0", "first", 0))
        assert entered.wait(1)
        for index in range(1, 6):
            worker.submit(TranslationUnit(str(index), str(index), index))
        assert len(worker._queue) == 2
        assert sum(r.state == "skipped" for r in results) == 3
        release.set()
        wait(lambda: sum(r.state == "ready" for r in results) == 3)
        assert [r.key for r in results if r.state == "ready"] == ["0", "4", "5"]
    finally:
        release.set()
        worker.close()


def test_retired_pending_request_cannot_publish():
    entered, release = threading.Event(), threading.Event()
    results = []

    class Slow(Translator):
        def translate(self, *args):
            entered.set()
            release.wait(3)
            return "obsolete"

    worker = TranslationWorker(Slow(), on_update=results.append, debounce_seconds=0)
    try:
        worker.submit(TranslationUnit("pending", "before", 0, pending=True))
        assert entered.wait(1)
        worker.reset_pending()
        release.set()
        time.sleep(0.15)
        assert not results
    finally:
        release.set()
        worker.close()
