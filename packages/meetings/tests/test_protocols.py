import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livecaptions_meetings import (
    ApiProfile,
    ApiProfileLibrary,
    MeetingStore,
    SummaryError,
    SummarySettings,
    SummaryWorker,
    TranscriptEntry,
    create_summary_client,
)
from livecaptions_meetings.protocols import endpoint_url


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic"])
@pytest.mark.parametrize("effort", ["default", "none", "low", "medium", "high", "xhigh", "max"])
def test_conversation_model_and_reasoning_wire_only_affect_chat(protocol, effort):
    settings = SummarySettings(
        "http://localhost/v1",
        "selected-model",
        "key",
        protocol=protocol,
        reasoning_effort=effort,
    )
    client = create_summary_client(settings)
    messages = [{"role": "user", "content": "会议问题"}]
    body = client.wire.conversation_body(settings, messages)
    assert body["model"] == "selected-model"
    summary = client.wire.body(settings, "summary")
    assert not {"reasoning_effort", "reasoning", "thinking", "output_config"} & summary.keys()
    if effort == "default":
        assert not {"reasoning_effort", "reasoning", "thinking", "output_config"} & body.keys()
    elif protocol == "chat_completions":
        assert body["reasoning_effort"] == effort
    elif protocol == "responses":
        assert body["reasoning"] == {"effort": effort}
    elif effort == "none":
        assert body["thinking"] == {"type": "disabled"}
    else:
        assert body["output_config"] == {"effort": effort}


def completed(protocol, text="第一条&#x20;结论。 test-secret"):
    if protocol == "chat_completions":
        return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
    if protocol == "responses":
        return {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "不要展示思考"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text}],
                },
            ],
        }
    return {
        "type": "message",
        "stop_reason": "end_turn",
        "content": [
            {"type": "thinking", "thinking": "不要展示思考"},
            {"type": "text", "text": text},
        ],
    }


@contextmanager
def server_response(payload, *, status=200, extra_headers=None):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(status)
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(
                payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "protocol,endpoint",
    [
        ("chat_completions", "/chat/completions"),
        ("responses", "/responses"),
        ("anthropic", "/messages"),
    ],
)
@pytest.mark.parametrize("complete_url", [False, True])
def test_real_http_wire_auth_input_and_output_for_each_protocol(protocol, endpoint, complete_url):
    with server_response(completed(protocol)) as (base, received):
        settings = SummarySettings(
            base + "/gateway/v1" + (endpoint if complete_url else ""),
            "chosen-model",
            "test-secret",
            system_prompt="只总结核心结论。",
            protocol=protocol,
        )
        text = create_summary_client(settings).summarize(
            "周会",
            [TranscriptEntry(1, "a", 3, 8, "周五交付。")],
            "此前确认了范围。",
        )
        assert text == "第一条 结论。 [redacted]"
        path, headers, body = received[0]
        assert path == "/gateway/v1" + endpoint
        assert body["model"] == "chosen-model" and body["stream"] is False
        if protocol == "responses":
            assert body["instructions"] == settings.system_prompt
            assert body["max_output_tokens"] == 4096 and body["store"] is False
            content = body["input"][0]["content"][0]["text"]
            assert "messages" not in body
        else:
            assert body["max_tokens"] == 4096
            assert body.get("system", body["messages"][0]["content"]) == settings.system_prompt
            content = body["messages"][-1]["content"]
        data = json.loads(content)
        assert data["previous_context"] == "此前确认了范围。"
        assert "00:00:03" in data["new_transcript"][0]
        assert "周五交付" in data["new_transcript"][0]
        if protocol == "anthropic":
            assert headers["x-api-key"] == "test-secret"
            assert headers["anthropic-version"] == "2023-06-01"
            assert headers["Authorization"] is None
        else:
            assert headers["Authorization"] == "Bearer test-secret"
            assert headers["x-api-key"] is None


@pytest.mark.parametrize(
    "protocol,payload",
    [
        ("responses", {"status": "incomplete", "output": []}),
        ("responses", {"status": "failed", "error": {"message": "test-secret"}}),
        ("responses", completed("responses", "")),
        ("responses", {"status": "completed", "output": [{"type": "function_call"}]}),
        (
            "anthropic",
            {
                "type": "message",
                "stop_reason": "max_tokens",
                "content": [{"type": "text", "text": "未完成"}],
            },
        ),
        ("anthropic", {"type": "message", "stop_reason": "tool_use", "content": []}),
        ("anthropic", completed("anthropic", "")),
        ("anthropic", {"type": "error", "error": {"message": "test-secret"}}),
    ],
)
def test_new_protocol_failures_preserve_entries_and_checkpoint(tmp_path, protocol, payload):
    with server_response(payload) as (base, _):
        store = MeetingStore.create(tmp_path, "保留记录")
        entry = store.append("a", 0, 3, "已有会议记录。")
        client = create_summary_client(
            SummarySettings(base, "test", "test-secret", protocol=protocol)
        )
        worker = SummaryWorker(store, client, automatic=False)
        worker.start()
        worker.close(drain=True)
        worker.wait(3)
        try:
            assert not worker.running and worker.last_error
            assert "test-secret" not in worker.last_error
            assert store.metadata["summary_cursor"] == 0 and store.entries() == [entry]
            assert not store.summaries()
        finally:
            store.close()


def test_profile_crud_drafts_and_secret_representations():
    profile = ApiProfile("a", "默认", "http://127.0.0.1:1/v1", "old", "secret-a")
    original = ApiProfileLibrary([profile], "a")
    draft = ApiProfileLibrary(original.profiles, original.selected_id)
    added = draft.add(
        "Responses 服务", "http://127.0.0.1:2/v1/responses", "new", "secret-b", "responses"
    )
    draft.update(added.id, name="远程服务")
    assert draft.selected.id == added.id
    assert draft.selected.summary_settings().protocol == "responses"
    assert len(original.profiles) == 1 and original.selected_id == "a"
    assert "secret-a" not in repr(profile) and "secret-b" not in repr(draft.profiles)
    draft.remove(added.id)
    assert draft.selected_id == "a"
    with pytest.raises(ValueError):
        draft.remove("a")


@pytest.mark.parametrize(
    "base,expected",
    [
        ("https://api.example.com", "https://api.example.com/v1/responses"),
        ("https://api.example.com/v1/", "https://api.example.com/v1/responses"),
        (
            "https://api.example.com/proxy/v1/chat/completions",
            "https://api.example.com/proxy/v1/responses",
        ),
    ],
)
def test_endpoint_normalization(base, expected):
    assert endpoint_url(base, "/responses") == expected


def test_http_errors_malformed_json_and_redirects_do_not_expose_or_forward_keys():
    for status, payload in [(401, b"test-secret"), (200, b"invalid json test-secret")]:
        with server_response(payload, status=status) as (base, _):
            with pytest.raises(SummaryError) as caught:
                create_summary_client(SummarySettings(base, "test", "test-secret")).summarize(
                    "x", []
                )
            assert "test-secret" not in str(caught.value)
    with server_response(completed("responses")) as (target, target_received):
        with (
            server_response({}, status=307, extra_headers={"Location": target + "/responses"}) as (
                base,
                _,
            ),
            pytest.raises(SummaryError, match="307"),
        ):
            create_summary_client(
                SummarySettings(base, "test", "test-secret", protocol="responses")
            ).summarize("x", [])
        assert not target_received
