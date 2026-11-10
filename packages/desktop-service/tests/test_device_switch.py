import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from livecaptions_service import DesktopRuntime, DesktopService, ModelSettings
from livecaptions_service.models import ModelManager


def test_legacy_default_saved_device_and_cli_override(tmp_path):
    path = tmp_path / "data/model-settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"selected_model": "qwen-1.7b"}), "utf-8")
    assert DesktopRuntime(tmp_path).device == "vulkan"
    ModelSettings("qwen-1.7b", device="cpu").save(path)
    assert DesktopRuntime(tmp_path).device == "cpu"
    assert DesktopRuntime(tmp_path, device="Vulkan1").device == "Vulkan1"
    assert ModelSettings.load(path).device == "cpu"
    for value in ("cuda", "", "GPU", 1, None):
        with pytest.raises(ValueError, match="设备"):
            ModelSettings(device=value)


def test_runtime_switch_releases_once_and_passes_device_to_engine(tmp_path, monkeypatch):
    import livecaptions_service.runtime as module

    import livecaptions_asr.engines.native_qwen as native

    opened, closed = [], []

    class Engine:
        def __init__(self, settings, **kwargs):
            opened.append(settings.device)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(native, "NativeQwenEngine", Engine)
    monkeypatch.setattr(module, "local_asset", lambda *args: tmp_path / "fixture.gguf")
    runtime = DesktopRuntime(tmp_path)
    first = runtime.recognizer()
    assert runtime.recognizer() is first and opened == ["vulkan"]
    settings = replace(runtime.model_settings, device="cpu")
    runtime.configure_models(settings)
    assert closed == [True] and runtime._recognizer is None
    second = runtime.recognizer()
    assert second is not first and opened == ["vulkan", "cpu"]
    runtime.configure_models(settings)
    assert runtime.recognizer() is second and len(closed) == 1
    runtime.configure_models(replace(settings, device="vulkan"))
    runtime.recognizer()
    assert opened == ["vulkan", "cpu", "vulkan"]
    runtime.close()
    assert len(closed) == 3


def test_service_switch_persistence_reject_active_and_failed_save(tmp_path, monkeypatch):
    events = []
    runtime = DesktopRuntime(tmp_path)
    service = DesktopService(
        tmp_path,
        runtime=runtime,
        observer=events.append,
        model_manager_factory=lambda root, observer: ModelManager(
            root, observer, locator=lambda *args: None
        ),
    )
    calls = []
    runtime._recognizer = SimpleNamespace(close=lambda: calls.append("close"))
    try:
        service.active = True
        with pytest.raises(RuntimeError, match="停止"):
            service.configure_models("qwen-0.6b", device="cpu")
        assert runtime.device == "vulkan" and calls == []
        assert not service.models.path.exists()
        service.active = False
        inventory = service.configure_models("qwen-0.6b", device="cpu")
        assert inventory["settings"]["device"] == runtime.device == "cpu"
        assert calls == ["close"]
        assert DesktopRuntime(tmp_path).device == "cpu"
        # Old callers omitting device preserve the selected CPU.
        service.configure_models("qwen-1.7b")
        assert runtime.device == "cpu"
        previous = service.models.path.read_text("utf-8")
        with pytest.raises(ValueError):
            service.configure_models("qwen-1.7b", device="cuda")
        assert service.models.path.read_text("utf-8") == previous

        def failed_save(*args):
            raise OSError("read-only")

        monkeypatch.setattr(ModelSettings, "save", failed_save)
        with pytest.raises(OSError):
            service.configure_models("qwen-1.7b", device="vulkan")
        assert runtime.device == service.models.settings.device == "cpu"
        assert [e.payload["device"] for e in events if e.name == "models.configured"] == [
            "cpu",
            "cpu",
        ]
    finally:
        service.active = False
        service.close()
