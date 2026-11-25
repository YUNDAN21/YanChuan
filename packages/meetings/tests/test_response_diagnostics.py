import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livecaptions_meetings import SummaryError, SummarySettings, create_summary_client
from test_protocols import completed, server_response


@pytest.mark.parametrize("only_reasoning", [False, True])
def test_empty_responses_preserve_safe_numeric_diagnostics(caplog, only_reasoning):
    payload = completed("responses", "")
    payload["usage"] = {
        "output_tokens": 4096,
        "output_tokens_details": {"reasoning_tokens": 4096 if only_reasoning else 0},
    }
    payload["provider_debug"] = "provider-private-content"
    with server_response(payload) as (base, _):
        client = create_summary_client(
            SummarySettings(base, "fixture-model", "fixture-secret", protocol="responses")
        )
        with (
            caplog.at_level(logging.INFO),
            pytest.raises(SummaryError, match="推理过程" if only_reasoning else "正文为空"),
        ):
            client.generate("private-meeting-text")
    assert "output_tokens=4096" in caplog.text
    assert "request started" in caplog.text and "request ended" in caplog.text
    assert not any(
        secret in caplog.text
        for secret in ("fixture-secret", "provider-private-content", "private-meeting-text")
    )


def test_periodic_response_bytes_do_not_extend_request_forever():
    complete = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                for _ in range(30):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    if complete.wait(0.015):
                        break
            except (BrokenPipeError, ConnectionError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = create_summary_client(
            SummarySettings(
                f"http://127.0.0.1:{server.server_port}/v1",
                "fixture",
                "fixture",
                timeout_seconds=0.08,
            )
        )
        started = time.monotonic()
        with pytest.raises(SummaryError, match="超时"):
            client.generate("test")
        assert time.monotonic() - started < 0.4
    finally:
        complete.set()
        server.shutdown()
        server.server_close()
