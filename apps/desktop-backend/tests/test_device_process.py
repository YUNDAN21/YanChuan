import json
import os
import subprocess
import sys


def test_saved_device_survives_real_process_restart_and_cli_override(tmp_path):
    def run(requests, *extra):
        result = subprocess.run(
            [sys.executable, "-u", "-m", "livecaptions_backend", "--root", str(tmp_path), *extra],
            check=False,
            input="\n".join(
                json.dumps({"id": i, "method": method, "params": params})
                for i, (method, params) in enumerate(requests, 1)
            )
            + "\n",
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        assert result.returncode == 0, result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]

    messages = run(
        [
            ("models.configure", {"selected_model": "qwen-0.6b", "device": "cpu"}),
            ("shutdown", {}),
        ]
    )
    assert next(m for m in messages if m.get("id") == 1)["result"]["settings"]["device"] == "cpu"
    assert any(m.get("name") == "models.configured" for m in messages)
    messages = run([("models.list", {}), ("shutdown", {})])
    assert messages[0]["result"]["settings"]["device"] == "cpu"
    messages = run([("models.list", {}), ("shutdown", {})], "--device", "vulkan")
    assert messages[0]["result"]["settings"]["device"] == "vulkan"
    # Session override alone never replaces the saved preference.
    messages = run([("models.list", {}), ("shutdown", {})])
    assert messages[0]["result"]["settings"]["device"] == "cpu"
