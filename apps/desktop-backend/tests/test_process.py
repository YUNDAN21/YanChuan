import json
import os
import subprocess
import sys
import threading
from io import StringIO

from livecaptions_backend.__main__ import serve
from livecaptions_service import DesktopService


def test_slow_model_discovery_does_not_block_control_requests(tmp_path):
    entered, released, stopped = threading.Event(), threading.Event(), threading.Event()

    class Service(DesktopService):
        def api_models(self, base_url, api_key="", protocol="chat_completions"):
            entered.set()
            assert released.wait(5)
            return ["draft-model"]

        def stop(self, **kwargs):
            stopped.set()
            super().stop(**kwargs)

    class Input:
        def __iter__(self):
            yield (
                json.dumps(
                    {
                        "id": 1,
                        "method": "profiles.models",
                        "params": {
                            "base_url": "http://localhost/v1",
                            "api_key": "draft",
                            "protocol": "responses",
                        },
                    }
                )
                + "\n"
            )
            assert entered.wait(3)
            yield '{"id":2,"method":"session.stop"}\n'
            assert stopped.is_set(), "Model discovery blocked stop"
            released.set()
            yield '{"id":3,"method":"shutdown"}\n'

    output = StringIO()
    serve(Input(), output, tmp_path, service_factory=Service)
    responses = {
        row["id"]: row
        for row in map(json.loads, output.getvalue().splitlines())
        if row["type"] == "response"
    }
    assert responses[1]["result"] == ["draft-model"]
    assert responses[2]["result"] is True


def test_real_child_utf8_handshake_unknown_request_and_shutdown(tmp_path):
    requests = [
        {"id": 1, "method": "hello"},
        {"id": 2, "method": "appearance.set", "params": {"mode": "dark"}},
        {"id": 3, "method": "unknown"},
        {"id": 4, "method": "shutdown"},
    ]
    result = subprocess.run(
        [sys.executable, "-u", "-m", "livecaptions_backend", "--root", str(tmp_path)],
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True,
        check=False,
        encoding="utf-8",
        timeout=20,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert result.returncode == 0, result.stderr
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    responses = {m["id"]: m for m in messages if m["type"] == "response"}
    assert responses[1]["result"]["protocol"] == 1
    assert responses[3]["error"]["message"] == "不支持的接口方法"
    assert responses[4]["result"]["closed"]
    assert any(m.get("name") == "appearance" and m["payload"] == "dark" for m in messages)


def test_chat_configuration_partial_updates_roundtrip(tmp_path):
    requests = [
        {
            "id": 1,
            "method": "chat.configure",
            "params": {"model": "chosen", "reasoning_effort": "high"},
        },
        {"id": 2, "method": "chat.configure", "params": {"context_tokens": 8192}},
        {"id": 3, "method": "configuration.get"},
        {"id": 4, "method": "shutdown"},
    ]
    output = StringIO()
    serve(StringIO("".join(json.dumps(request) + "\n" for request in requests)), output, tmp_path)
    responses = {
        row["id"]: row
        for row in map(json.loads, output.getvalue().splitlines())
        if row["type"] == "response"
    }
    configuration = responses[3]["result"]
    assert configuration["chat_model"] == "chosen"
    assert configuration["chat_reasoning_effort"] == "high"
    assert configuration["chat_context_tokens"] == 8192


def test_eof_closes_library_and_invalid_input_survives(tmp_path):
    instances = []

    def factory(*args, **kwargs):
        service = DesktopService(*args, **kwargs)
        instances.append(service)
        return service

    output = StringIO()
    serve(
        StringIO('invalid\n[]\n{"id":7,"method":"snapshot"}\n'),
        output,
        tmp_path,
        service_factory=factory,
    )
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(messages) == 3
    assert messages[-1]["id"] == 7
    assert instances[0]._closing
