import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livecaptions_service import DesktopRuntime, DesktopService, ModelManager, ModelSettings
from livecaptions_service.models import ModelAsset, asset_by_id, download_asset, local_asset


def qwen_files(directory):
    directory.mkdir(parents=True)
    for name in [
        "config.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "model.safetensors",
    ]:
        (directory / name).write_text("fixture", "utf-8")


def test_empty_inventory_legacy_cache_and_model_selection(tmp_path):
    manager = ModelManager(tmp_path)
    assert not any(a["ready"] for a in manager.inventory()["assets"])
    legacy = tmp_path / "models/huggingface/modelscope/models/Qwen--Qwen3-ASR-0.6B/snapshots/master"
    qwen_files(legacy)
    assert local_asset(asset_by_id("qwen-0.6b"), tmp_path / "models") is None
    assert not manager.inventory()["assets"][0]["ready"]
    assert (legacy / "model.safetensors").exists()
    manager.configure(ModelSettings("qwen-1.7b", "huggingface", str(tmp_path / "other")))
    restored = ModelManager(tmp_path)
    assert restored.settings.selected_model == "qwen-1.7b"
    assert restored.settings.storage(tmp_path) == tmp_path / "other"
    with pytest.raises(ValueError):
        ModelSettings("silero")
    with pytest.raises(ValueError):
        ModelSettings(directory="relative/path")
    manager.close()


def test_endpoint_download_progress_hash_failure_and_retry(tmp_path):
    payload = b"synthetic-model-fixture" * 100

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = tmp_path / "endpoint/fixture.onnx"
    target.parent.mkdir()
    target.write_bytes(b"previous-incomplete")
    try:
        url = f"http://127.0.0.1:{server.server_port}/fixture"
        invalid = ModelAsset(
            "fixture", "fixture", "segmentation", filename="fixture.onnx", url=url, sha256="wrong"
        )
        with pytest.raises(ValueError, match="校验"):
            download_asset(invalid, tmp_path, "modelscope", lambda value: None)
        assert target.read_bytes() == b"previous-incomplete"
        valid = ModelAsset(
            "fixture",
            "fixture",
            "segmentation",
            filename="fixture.onnx",
            url=url,
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        progress = []
        assert download_asset(valid, tmp_path, "modelscope", progress.append) == target
        assert target.read_bytes() == payload
        assert progress[-1]["current"] == len(payload)
        assert download_asset(valid, tmp_path, "modelscope", progress.append) == target
        assert "跳过" in progress[-1]["message"]
    finally:
        server.shutdown()
        server.server_close()


def test_runtime_switch_releases_models_and_missing_assets_are_actionable(tmp_path):
    runtime = DesktopRuntime(tmp_path)
    from types import SimpleNamespace

    runtime._recognizer = SimpleNamespace(close=lambda: None)
    runtime._vad = runtime._endpoint = object()
    settings = ModelSettings("qwen-1.7b", directory=str(tmp_path / "storage"))
    runtime.configure_models(settings)
    assert runtime._recognizer is runtime._vad is runtime._endpoint is None
    with pytest.raises(RuntimeError, match="模型管理"):
        runtime.recognizer()


def test_service_configuration_api_and_active_guard(tmp_path):
    service = DesktopService(tmp_path)
    assert service.model_inventory()["settings"]["selected_model"] == "qwen-0.6b"
    service.configure_models("qwen-1.7b", "huggingface", str(tmp_path / "storage"))
    assert (
        json.loads((tmp_path / "data/model-settings.json").read_text("utf-8"))["selected_model"]
        == "qwen-1.7b"
    )
    service.active = True
    with pytest.raises(RuntimeError, match="停止转写"):
        service.configure_models("qwen-0.6b")
    service.active = False
    service.close()
