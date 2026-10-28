import json
from pathlib import Path

import pytest
from livecaptions_service import DesktopService, ModelSettings
from livecaptions_service.models import ModelManager
from livecaptions_service.runtime import DesktopRuntime


def test_native_inventory_keeps_selection_directory_and_does_not_accept_legacy_cache(tmp_path):
    directory = tmp_path / "自定义 模型目录"
    directory.mkdir()
    (directory / "model.safetensors").write_bytes(b"old retained weights")
    settings = ModelSettings("qwen-1.7b", directory=str(directory))
    settings.save(tmp_path / "data/model-settings.json")
    manager = ModelManager(tmp_path)
    inventory = manager.inventory()
    assert inventory["settings"]["selected_model"] == "qwen-1.7b"
    assert Path(inventory["directory"]) == directory
    assert not any(asset["ready"] for asset in inventory["assets"])
    assert "Q8" in inventory["assets"][0]["name"]
    assert inventory["sources"] == ("huggingface",)
    assert inventory["settings"]["source"] == "huggingface"
    assert (directory / "model.safetensors").read_bytes() == b"old retained weights"
    assert json.loads(manager.path.read_text("utf-8"))["directory"] == str(directory)
    manager.close()


def test_bad_explicit_file_does_not_replace_saved_configuration(tmp_path):
    manager = ModelManager(tmp_path)
    settings = ModelSettings("qwen-1.7b", "huggingface", str(tmp_path))
    manager.configure(settings)
    previous = manager.path.read_bytes()
    malformed = tmp_path / "其他实现.gguf"
    malformed.write_bytes(b"GGUF\x03")
    with pytest.raises(ValueError, match="不兼容"):
        manager.configure(ModelSettings("qwen-1.7b", "huggingface", str(tmp_path), str(malformed)))
    assert manager.path.read_bytes() == previous
    manager.close()


def test_native_runtime_releases_models_on_switch_and_service_shutdown(tmp_path):
    class Recognizer:
        calls = 0

        def close(self):
            self.calls += 1

    runtime = DesktopRuntime(tmp_path, library_path=tmp_path / "missing.dll")
    recognizer = Recognizer()
    runtime._recognizer = recognizer
    runtime._endpoint = runtime._vad = object()
    runtime.configure_models(ModelSettings("qwen-1.7b"))
    assert recognizer.calls == 1
    assert runtime._recognizer is runtime._vad is runtime._endpoint is None
    runtime._recognizer = recognizer
    service = DesktopService(tmp_path, runtime=runtime)
    service.close()
    service.close()
    assert recognizer.calls == 2
    with pytest.raises(RuntimeError, match="GGUF"):
        runtime.recognizer()
