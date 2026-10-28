import json
import threading
import time
from types import SimpleNamespace

import pytest
from livecaptions_meetings import ApiProfile
from livecaptions_service import DesktopService, InputSelection
from livecaptions_service.config import MeetingSettings

from livecaptions_asr.results import Segment, Sentence, StreamEvent


def test_model_discovery_uses_unsaved_draft_without_changing_configuration(tmp_path, monkeypatch):
    from livecaptions_service import service as module

    service = DesktopService(tmp_path)
    before = service.configuration()
    requests = []
    monkeypatch.setattr(
        module, "list_api_models", lambda settings: requests.append(settings) or ["remote"]
    )
    try:
        assert service.api_models("http://localhost:9999/v1", "draft-key", "responses") == [
            "remote"
        ]
        assert requests[0].api_key == "draft-key" and requests[0].protocol == "responses"
        assert service.configuration() == before
        assert not service.settings_path.exists()
    finally:
        service.close()


class Runtime:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.prepares = 0
        self.on_progress = None

    def runner(self, selection):
        self.prepares += 1
        return self

    def run(self, language, stop, cancel, on_events, on_state, *, on_progress):
        on_state("recording")
        on_events([StreamEvent("partial", segment=Segment(0, 0, 1, "测试", final=False))])
        self.entered.set()
        while not stop.wait(0.01) and not self.release.is_set():
            pass
        if not cancel.is_set():
            on_events(
                [
                    StreamEvent(
                        "sentence",
                        sentence=Sentence(0, 0, 2, "测试完成。", (0,), "semantic_endpoint"),
                    )
                ]
            )


class Client:
    model = "test"
    settings = SimpleNamespace(api_key="local-secret")

    def summarize(self, title, entries, previous):
        return "## 讨论\n测试完成。"


def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def configured(root):
    settings = MeetingSettings("http://127.0.0.1:1/v1", "test", "local-secret")
    settings.save(root / "data/meeting-settings.json")
    from livecaptions_service.translation import CaptionSettings

    CaptionSettings(enabled=False).save(root / "data/caption-settings.json")


def test_no_credentials_still_records_and_single_session_drains(tmp_path):
    runtime, events = Runtime(), []
    service = DesktopService(tmp_path, runtime=runtime, observer=events.append)
    service.start(title="无密钥会议")
    assert runtime.entered.wait(3)
    with pytest.raises(RuntimeError):
        service.start("captions")
    service.stop()
    assert service.wait(5)
    assert service.store.entries()[0].text == "测试完成。"
    assert service.store.metadata["status"] == "ended"
    assert service.store.metadata["summary_cursor"] == 0
    assert any(e.name == "summary.error" for e in events)
    service.close()


def test_record_summary_export_reopen_and_caption_mode_reuses_runtime(tmp_path):
    configured(tmp_path)
    runtime = Runtime()
    service = DesktopService(tmp_path, runtime=runtime, client_factory=lambda _: Client())
    service.start(title="会议")
    assert runtime.entered.wait(3)
    service.stop()
    assert service.wait(5)
    directory = service.store.directory
    assert len(service.store.summaries()) == 1
    assert service.store.metadata["summary_cursor"] == 1
    assert "local-secret" not in (directory / "summaries.md").read_text(encoding="utf-8")
    service.open_meeting(directory)
    assert len(service.snapshot()["meeting"]["entries"]) == 1
    service.start("captions")
    service.stop()
    assert service.wait(5)
    assert service.store is None
    assert len(service.list_meetings()) == 1
    assert runtime.prepares == 2
    service.close()


def test_manual_summary_and_failure_checkpoint(tmp_path):
    configured(tmp_path)
    service = DesktopService(tmp_path, runtime=Runtime(), client_factory=lambda _: Client())
    service.configure_schedule(5, False)
    service.start(title="手动")
    assert service.runtime.entered.wait(3)
    service.stop()
    assert service.wait(5)
    assert not service.store.summaries()

    class Failed(Client):
        def summarize(self, *args):
            raise RuntimeError("local-secret 错误")

    service.client_factory = lambda _: Failed()
    service.summarize_now()
    wait(lambda: service.worker.last_error)
    assert service.store.metadata["summary_cursor"] == 0
    assert "local-secret" not in service.worker.last_error
    service.worker.set_client(Client())
    service.summarize_now()
    wait(lambda: service.store.metadata["summary_cursor"] == 1)
    wait(lambda: not service.worker.busy)
    service.close()


def test_legacy_configuration_roundtrip_and_atomic_profile_selection(tmp_path):
    path = tmp_path / "data/meeting-settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1/v1",
                "model": "old",
                "api_key": "kept",
                "summary_prompt": "自定义",
            }
        ),
        encoding="utf-8",
    )
    service = DesktopService(tmp_path)
    assert service.settings.template_library().selected.prompt == "自定义"
    original = service.settings.selected_api_profile
    with pytest.raises(ValueError):
        service.set_api_profiles(
            [ApiProfile("blank", "空", "http://localhost/v1", "", "")], "blank"
        )
    assert service.settings.selected_api_profile == original
    service.set_api_profiles(
        [ApiProfile("new", "新配置", "http://localhost/v1", "m", "k", "responses")], "new"
    )
    service.set_appearance("dark")
    service.close()
    other = DesktopService(tmp_path)
    assert other.appearance.mode == "dark"
    assert other.settings.api_key == "k"
    assert other.settings.summary_prompt == "自定义"
    other.close()


def test_stop_during_prepare_never_opens_capture(tmp_path):
    class Preparing(Runtime):
        def runner(self, selection):
            self.entered.set()
            self.release.wait(3)
            return self

        def run(self, language, stop, cancel, *args, **kwargs):
            assert stop.is_set()

    runtime = Preparing()
    service = DesktopService(tmp_path, runtime=runtime)
    service.start("captions")
    assert runtime.entered.wait(2)
    service.stop()
    runtime.release.set()
    assert service.wait(3)
    service.close()


@pytest.mark.parametrize("selection", [{"source": "bad"}, {"language": "bad"}])
def test_invalid_selection(selection):
    with pytest.raises(ValueError):
        InputSelection(**selection)


def test_event_sequence_and_close_drain(tmp_path):
    events, runtime = [], Runtime()
    service = DesktopService(tmp_path, runtime=runtime, observer=events.append)
    service.start("captions")
    assert runtime.entered.wait(2)
    service.close()
    assert not service.active
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert service.history.confirmed[0].text == "测试完成。"
    assert len({e.session_id for e in events}) == 1


def test_summary_client_switch_keeps_inflight_snapshot(tmp_path):
    configured(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class Slow(Client):
        model = "old"

        def summarize(self, *args):
            entered.set()
            release.wait(4)
            return "旧请求结果"

    service = DesktopService(tmp_path, runtime=Runtime(), client_factory=lambda _: Slow())
    service.configure_schedule(5, False)
    service.start()
    assert service.runtime.entered.wait(2)
    service._ingest([StreamEvent("sentence", sentence=Sentence(3, 0, 1, "先总结。", (3,), "end"))])
    service.summarize_now()
    assert entered.wait(2)
    service.client_factory = lambda _: Client()
    service.set_api_profiles(
        [ApiProfile("next", "下一个", "http://localhost/v1", "test", "new")], "next"
    )
    release.set()
    wait(lambda: bool(service.store.summaries()))
    assert service.store.summaries()[0].model == "old"
    service.stop()
    assert service.wait(5)
    assert service.store.summaries()[-1].model == "test"
    service.close()


def test_file_chunks_project_semantic_captions_and_reuse_model(tmp_path):
    import numpy as np
    import soundfile as sf

    from livecaptions_asr import Recognizer
    from livecaptions_asr.domain import EngineStatus, Recognition

    class Engine:
        loads = 0

        def status(self):
            return EngineStatus("test", state="ready" if self.loads else "unloaded")

        def load(self):
            if not self.loads:
                self.loads += 1

        def recognize(self, audio, language, context):
            return Recognition("长文件按语义分段。继续识别下一部分。", "Chinese")

    engine = Engine()

    class FileRuntime:
        on_progress = None
        cached = Recognizer(engine)

        def recognizer(self):
            return self.cached

    audio = tmp_path / "long.wav"
    sf.write(audio, 0.1 * np.sin(np.arange(16000 * 65) * 0.12), 16000)
    service = DesktopService(tmp_path, runtime=FileRuntime())
    service.configure_schedule(5, False)
    for _ in range(2):
        service.start("file", file=audio, title="长文件")
        assert service.wait(10)
        assert service.state == "ended"
        assert len(service.store.entries()) >= 3
        assert service.history.pending is None
    assert service.history.confirmed
    assert engine.loads == 1
    assert len(service.list_meetings()) == 2
    service.close()


def test_templates_cancel_isolation_restore_defaults_and_validation(tmp_path):
    service = DesktopService(tmp_path)
    templates = service.configuration()["prompt_templates"]
    templates[0]["prompt"] = "新的中文提示词"
    assert service.settings.summary_prompt != "新的中文提示词"
    service.set_templates(templates, templates[0]["id"])
    assert service.settings.summary_prompt == "新的中文提示词"
    with pytest.raises(ValueError):
        service.set_templates([], "")
    assert service.settings.summary_prompt == "新的中文提示词"
    defaults = service.default_templates()
    assert defaults[0]["prompt"] != "新的中文提示词"
    service.close()
