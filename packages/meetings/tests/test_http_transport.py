import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livecaptions_meetings import ModelCatalogSettings, SummarySettings, list_api_models
from livecaptions_meetings.chat import ChatCoordinator
from livecaptions_meetings.client import create_summary_client
from livecaptions_meetings.http_transport import ProviderHttpError
from livecaptions_meetings.model_catalog import ModelCatalogError
from livecaptions_meetings.store import MeetingStore


@contextmanager
def provider(status, content_type):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(dict(self.headers))
            # A fixture gateway rejects unidentified Python clients.
            identity_ok = self.headers.get("User-Agent", "").startswith("YanChuan/")
            self.send_response(status if identity_ok else 403)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            payload = {"data": [{"id": "fixture"}]} if status == 200 else {"secret": "private-key"}
            self.wfile.write(json.dumps(payload).encode())

        do_POST = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("operation", ["models", "generate", "chat"])
@pytest.mark.parametrize(
    "status,content_type,message",
    [
        (403, "text/html", "防护页面"),
        (403, "application/json", "权限"),
        (401, "application/json", "认证失败"),
    ],
)
def test_safe_http_classification_for_all_operations(operation, status, content_type, message):
    with provider(status, content_type) as (base, requests):
        settings = SummarySettings(base, "fixture", "private-key")
        client = create_summary_client(settings)
        with pytest.raises((ProviderHttpError, ModelCatalogError), match=message) as caught:
            if operation == "models":
                list_api_models(ModelCatalogSettings(base, settings.api_key))
            elif operation == "generate":
                client.generate("fixture")
            else:
                client.chat([{"role": "user", "content": "fixture"}])
        assert "private-key" not in str(caught.value)
        assert len(requests) == 1
        assert requests[0]["User-Agent"] == "YanChuan/0.2.0"
        assert requests[0]["Authorization"] == "Bearer private-key"


def test_model_catalog_passes_application_identity_gate():
    with provider(200, "application/json") as (base, requests):
        assert list_api_models(ModelCatalogSettings(base, "private-key")) == ["fixture"]
        assert len(requests) == 1


def test_chat_persists_safe_gateway_diagnostic(tmp_path):
    store = MeetingStore.create(tmp_path, "fixture")
    complete = threading.Event()
    coordinator = ChatCoordinator(
        lambda value: (
            complete.set() if any(m["status"] == "failed" for m in value["messages"]) else None
        )
    )
    try:
        with provider(403, "text/html") as (base, _):
            client = create_summary_client(SummarySettings(base, "fixture", "private-key"))
            coordinator.send(store.directory, store.context_snapshot(), "测试", client)
            assert complete.wait(2)
            answer = coordinator.list(store.directory)[-1]
            assert "防护页面" in answer.error and "private-key" not in answer.error
    finally:
        coordinator.close()
        store.close()
