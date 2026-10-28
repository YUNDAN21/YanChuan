import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livecaptions_meetings import ModelCatalogError, ModelCatalogSettings, list_api_models


@contextmanager
def server(replies):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
            status, payload = replies[min(len(received) - 1, len(replies) - 1)]
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Location", "/redirect-must-not-be-followed")
            self.end_headers()
            self.wfile.write(body)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", received
    finally:
        http.shutdown()
        http.server_close()


@pytest.mark.parametrize(
    "protocol,suffix",
    [
        ("chat_completions", "/chat/completions"),
        ("responses", "/responses"),
        ("anthropic", "/messages"),
    ],
)
def test_discovery_uses_draft_without_model_and_correct_auth(protocol, suffix):
    with server([(200, {"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}]})]) as (
        base,
        calls,
    ):
        settings = ModelCatalogSettings(base + "/gateway/v1" + suffix, "draft-secret", protocol)
        assert "draft-secret" not in repr(settings)
        assert list_api_models(settings) == ["a-model", "z-model"]
        path, headers = calls[0]
        assert path.split("?")[0] == "/gateway/v1/models"
        if protocol == "anthropic":
            assert headers["x-api-key"] == "draft-secret"
            assert headers["anthropic-version"] == "2023-06-01"
            assert "authorization" not in headers
        else:
            assert headers["authorization"] == "Bearer draft-secret"


def test_anthropic_paging_and_deduplication():
    with server(
        [
            (200, {"data": [{"id": "first"}], "has_more": True, "last_id": "first"}),
            (200, {"data": [{"id": "second"}, {"id": "first"}], "has_more": False}),
        ]
    ) as (base, calls):
        assert list_api_models(ModelCatalogSettings(base, "key", "anthropic")) == [
            "first",
            "second",
        ]
        assert calls[1][0] == "/v1/models?limit=1000&after_id=first"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 302])
def test_error_body_and_redirect_do_not_expose_credentials(status):
    with server([(status, b"draft-secret sensitive server body")]) as (base, calls):
        with pytest.raises(ModelCatalogError) as error:
            list_api_models(ModelCatalogSettings(base, "draft-secret"))
        assert f"HTTP {status}" in str(error.value)
        assert "draft-secret" not in str(error.value)
        assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"invalid json",
        [],
        {"data": {}},
        {"data": [{}]},
        {"data": [{"id": "draft-secret"}]},
        {"data": [], "has_more": True, "last_id": ""},
    ],
)
def test_invalid_payload_is_actionable(payload):
    with server([(200, payload)]) as (base, _), pytest.raises(ModelCatalogError):
        list_api_models(ModelCatalogSettings(base, "draft-secret"))


def test_empty_list_and_repeated_cursor():
    with server([(200, {"data": []})]) as (base, _):
        assert list_api_models(ModelCatalogSettings(base, "")) == []
    with server([(200, {"data": [{"id": "a"}], "has_more": True, "last_id": "a"})]) as (
        base,
        calls,
    ):
        with pytest.raises(ModelCatalogError, match="分页"):
            list_api_models(ModelCatalogSettings(base, "", "anthropic"))
        assert len(calls) == 2


def test_invalid_header_never_echoes_draft_key():
    with pytest.raises(ModelCatalogError) as error:
        ModelCatalogSettings("http://localhost", "draft-secret\ninjected")
    assert "draft-secret" not in str(error.value)
