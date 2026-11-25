"""Stopping a manual-summary meeting must preserve its unrequested tail."""

import threading
import time
from types import SimpleNamespace

from livecaptions_service import DesktopService
from livecaptions_service.config import MeetingSettings

from livecaptions_asr.results import Sentence, StreamEvent


def wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_manual_mode_does_not_request_new_tail_on_stop(tmp_path):
    settings = MeetingSettings("http://localhost/v1", "fake", "fixture")
    settings.automatic_summary = False
    settings.save(tmp_path / "data/meeting-settings.json")
    ready = threading.Event()
    calls = []

    class Runtime:
        on_progress = None

        def runner(self, selection):
            return self

        def run(self, language, stop, cancel, on_events, on_state, **kwargs):
            on_state("recording")
            on_events(
                [StreamEvent("sentence", sentence=Sentence(0, 0, 2, "第一段。", (0,), "end"))]
            )
            ready.set()
            assert stop.wait(3)
            on_events(
                [
                    StreamEvent(
                        "sentence", sentence=Sentence(1, 2, 4, "停止前的第二段。", (1,), "end")
                    )
                ]
            )

    def summarize(title, entries, previous):
        calls.append([entry.text for entry in entries])
        return "测试总结"

    service = DesktopService(
        tmp_path,
        runtime=Runtime(),
        client_factory=lambda _: SimpleNamespace(model="fake", summarize=summarize),
    )
    try:
        service.start()
        assert ready.wait(2)
        assert service.summarize_now()
        wait(lambda: service.store.metadata["summary_cursor"] == 1 and not service.worker.busy)
        assert len(calls) == 1
        service.stop()
        assert service.wait(3)
        assert len(calls) == 1
        assert service.store.entries()[-1].text == "停止前的第二段。"
        assert service.store.metadata["summary_cursor"] == 1
    finally:
        service.close()
