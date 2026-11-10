from concurrent.futures import Future

import pytest
from livecaptions_service import DesktopService, InputSelection
from test_service import Runtime


class SwitchingRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.requests = []

    def set_source(self, source):
        future = Future()
        self.requests.append((source, future))
        return future

    def run(self, *args, **kwargs):
        try:
            super().run(*args, **kwargs)
        finally:
            for _, future in self.requests:
                if not future.done():
                    future.set_exception(RuntimeError("录音已结束"))


def test_meeting_switch_keeps_store_stream_and_emits_confirmed_state(tmp_path):
    runtime, events = SwitchingRuntime(), []
    service = DesktopService(tmp_path, runtime=runtime, observer=events.append)
    try:
        result = service.start(selection=InputSelection(source="system"))
        assert runtime.entered.wait(3)
        store, session_id = service.store, result["session_id"]
        for source in ("microphone", "both", "system"):
            result = service.set_source(source, session_id=session_id)
            assert result["switching"]
            assert result["source"] == service.source
            assert service.snapshot()["audio_source"]["requested_source"] == source
            with pytest.raises(RuntimeError, match="等待"):
                service.set_source("both")
            runtime.requests[-1][1].set_result(source)
            assert service.source == source
            assert not service.snapshot()["audio_source"]["switching"]
            assert service.store is store and service.session_id == session_id
        assert runtime.prepares == 1
        assert service.active and service.state == "recording"
        service.stop()
        assert service.wait(3)
        assert store.entries()[0].text == "测试完成。"
        assert store.entries()[0].end_seconds == 2
        assert len(service.list_meetings()) == 1
        changes = [event for event in events if event.name == "session.source"]
        assert all(event.session_id == session_id for event in changes)
        assert changes[-1].payload["source"] == "system"
    finally:
        service.close()


def test_switch_failure_rolls_back_without_ending_meeting_and_can_retry(tmp_path):
    runtime, events = SwitchingRuntime(), []
    service = DesktopService(tmp_path, runtime=runtime, observer=events.append)
    try:
        service.start(selection=InputSelection(source="system"))
        assert runtime.entered.wait(3)
        service.set_source("both")
        runtime.requests[-1][1].set_exception(OSError("设备不可用"))
        assert service.source == "system" and service.active
        assert events[-1].name == "session.source"
        assert events[-1].payload["error"] == "设备不可用"
        service.set_source("microphone")
        runtime.requests[-1][1].set_result("microphone")
        assert service.source == "microphone"
    finally:
        service.close()


def test_switch_guards_and_stop_with_pending_change(tmp_path):
    runtime = SwitchingRuntime()
    service = DesktopService(tmp_path, runtime=runtime)
    try:
        with pytest.raises(RuntimeError, match="录音过程中"):
            service.set_source("system")
        service.start()
        assert runtime.entered.wait(3)
        with pytest.raises(RuntimeError, match="会议已切换"):
            service.set_source("system", session_id="stale-session")
        with pytest.raises(ValueError):
            service.set_source("invalid")
        service._state("preparing")
        with pytest.raises(RuntimeError, match="录音过程中"):
            service.set_source("system")
        service._state("recording")
        service.set_source("system")
        service.stop()
        with pytest.raises(RuntimeError, match="录音过程中"):
            service.set_source("microphone")
        assert service.wait(3)
        assert not service.snapshot()["audio_source"]["switching"]
        assert service.store.entries()
        assert runtime.requests[-1][1].done()
    finally:
        service.close()


def test_captions_do_not_accept_meeting_source_command(tmp_path):
    runtime = SwitchingRuntime()
    service = DesktopService(tmp_path, runtime=runtime)
    try:
        service.start("captions")
        assert runtime.entered.wait(3)
        with pytest.raises(RuntimeError, match="会议录音"):
            service.set_source("microphone")
    finally:
        service.close()
