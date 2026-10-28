import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livecaptions_meetings import ApiProfile
from livecaptions_service.translation import ApiTranslator


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic"])
def test_translation_uses_existing_wire_protocol_without_meeting_prompt(protocol):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, dict(self.headers), body))
            if protocol == "chat_completions":
                response = {
                    "choices": [{"finish_reason": "stop", "message": {"content": "Hello."}}]
                }
            elif protocol == "responses":
                response = {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Hello."}],
                        }
                    ],
                }
            else:
                response = {
                    "type": "message",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "Hello."}],
                }
            raw = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        translator = ApiTranslator(
            ApiProfile(
                "test",
                "test",
                f"http://127.0.0.1:{server.server_port}/v1",
                "model",
                "safe-test-key",
                protocol,
            )
        )
        assert translator.translate("こんにちは。", "Japanese", "en") == "Hello."
        path, headers, body = requests[0]
        assert path.endswith(
            {
                "chat_completions": "/chat/completions",
                "responses": "/responses",
                "anthropic": "/messages",
            }[protocol]
        )
        assert body["model"] == "model"
        assert "new_transcript" not in json.dumps(body)
        if protocol == "responses":
            prompt, content = body["instructions"], body["input"][0]["content"][0]["text"]
            assert body["store"] is False
        elif protocol == "anthropic":
            prompt, content = body["system"], body["messages"][0]["content"]
            assert headers["X-Api-Key"] == "safe-test-key"
        else:
            prompt, content = body["messages"][0]["content"], body["messages"][1]["content"]
        assert "字幕翻译员" in prompt
        assert json.loads(content) == {
            "text": "こんにちは。",
            "source_language": "Japanese",
            "target_language": "English",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
