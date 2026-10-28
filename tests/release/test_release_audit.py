import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "package_desktop", Path(__file__).resolve().parents[2] / "scripts/package_desktop.py"
)
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


def test_release_rejects_state_weights_and_secret_bytes(tmp_path):
    (tmp_path / "models").mkdir()
    weight = tmp_path / "models/fixture.bin"
    weight.write_bytes(b"model")
    with pytest.raises(ValueError, match="excluded"):
        packaging.audit_release(tmp_path, [])
    weight.unlink()
    private = tmp_path / "config.json"
    private.write_bytes(b"before private-release-key after")
    with pytest.raises(ValueError, match="credentials"):
        packaging.audit_release(tmp_path, [b"private-release-key"])
    private.unlink()
    model = tmp_path / "model.safetensors"
    model.write_bytes(b"weights")
    with pytest.raises(ValueError, match="excluded"):
        packaging.audit_release(tmp_path, [])


def test_release_manifest_and_secret_spanning_scan_blocks(tmp_path):
    binary = tmp_path / "runtime.dll"
    binary.write_bytes(b"a" * (4 * 1024 * 1024 - 5) + b"boundary-secret" + b"b" * 8)
    with pytest.raises(ValueError, match="credentials"):
        packaging.audit_release(tmp_path, [b"boundary-secret"])
    report = packaging.audit_release(tmp_path, [])
    assert report["credential_scan_passed"]
    assert report["files"][0]["bytes"] == binary.stat().st_size
    assert "sha256" not in report["files"][0]


@pytest.mark.parametrize(
    "name",
    [
        "fixture.gguf",
        "archive/pytorch/qwen.py",
        "backend/python/Lib/site-packages/torch/x.py",
        "backend/native/cublas64_12.dll",
        "backend/python/Lib/site-packages/transformers-4.dist-info/METADATA",
    ],
)
def test_native_release_rejects_models_archive_and_obsolete_dependencies(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    with pytest.raises(ValueError):
        packaging.audit_release(tmp_path, [], native_only=True)
