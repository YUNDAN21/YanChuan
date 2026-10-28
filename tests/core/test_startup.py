import pytest

from livecaptions_asr import Recognizer, StartupEvent, StartupTimer
from livecaptions_asr.domain import Recognition


def terminal(events):
    return [event for event in events if event.state != "started"]


def test_phase_reports_failure_and_preserves_the_exception(monkeypatch):
    times = iter([2.0, 5.25])
    monkeypatch.setattr("livecaptions_asr.startup.perf_counter", lambda: next(times))
    events = []
    with pytest.raises(ValueError, match="model failed"), StartupTimer(events.append).phase("load"):
        raise ValueError("model failed")
    assert events == [StartupEvent("load", "started"), StartupEvent("load", "failed", 3.25)]


def test_observer_failure_does_not_interrupt_loading(caplog):
    def broken(event):
        raise RuntimeError("display unavailable")

    with StartupTimer(broken).phase("load"):
        pass
    assert "Startup observer failed" in caplog.text


def test_prepare_reports_warmup_reuse_and_failure_recovery():
    class Engine:
        calls = 0

        def load(self):
            pass

        def recognize(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("warmup failed")
            return Recognition("", "unknown")

    engine = Engine()
    recognizer = Recognizer(engine)
    events = []
    with pytest.raises(ValueError, match="warmup failed"):
        recognizer.prepare(on_progress=events.append)
    assert any(event.stage == "warmup" and event.state == "failed" for event in events)
    recognizer.prepare(on_progress=events.append)
    recognizer.prepare(on_progress=events.append)
    assert engine.calls == 2
    assert terminal(events)[-2:] == [
        StartupEvent("warmup", "reused"),
        next(event for event in reversed(events) if event.stage == "recognizer_prepare"),
    ]
