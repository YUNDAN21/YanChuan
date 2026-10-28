import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livecaptions_service.native_assets import download_verified


@pytest.mark.parametrize("mode", ["range", "ignore-range", "wrong-range", "corrupt"])
def test_download_resume_validates_response_and_never_replaces_good_target(tmp_path, mode):
    payload = b"model-fixture" * 100
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append(self.headers.get("Range"))
            offset = 100
            if mode == "ignore-range":
                self.send_response(200)
                content = payload
            else:
                self.send_response(206)
                self.send_header(
                    "Content-Range",
                    f"bytes {101 if mode == 'wrong-range' else offset}-{len(payload) - 1}/{len(payload)}",
                )
                content = payload[offset:]
            if mode == "corrupt":
                content = b"X" * len(content)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = tmp_path / "模型.gguf"
    target.write_bytes(b"preserved previous model")
    partial = target.with_suffix(".gguf.download")
    partial.write_bytes(payload[:100])
    try:
        call = lambda: download_verified(
            f"http://127.0.0.1:{server.server_port}/model",
            target,
            len(payload),
            hashlib.sha256(payload).hexdigest(),
            lambda update: None,
        )
        if mode in {"wrong-range", "corrupt"}:
            with pytest.raises(ValueError):
                call()
            assert target.read_bytes() == b"preserved previous model"
        else:
            assert call() == target and target.read_bytes() == payload
            assert call() == target  # completed file is reused without another request
        assert received == ["bytes=100-"]
    finally:
        server.shutdown()
        server.server_close()


def test_cancel_callback_preserves_partial_then_resumes_without_publishing(tmp_path):
    payload = b"x" * (1024 * 1024)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            start = int(self.headers.get("Range", "bytes=0-")[6:-1])
            requests.append(start)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
            self.send_header("Content-Length", str(len(payload) - start))
            self.end_headers()
            try:
                self.wfile.write(payload[start:])
            except (ConnectionAbortedError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = tmp_path / "resume.gguf"
    target.write_bytes(b"existing")
    checksum = hashlib.sha256(payload).hexdigest()

    def cancel(update):
        raise InterruptedError("cancelled")

    try:
        url = f"http://127.0.0.1:{server.server_port}/model"
        with pytest.raises(InterruptedError):
            download_verified(url, target, len(payload), checksum, cancel)
        assert target.read_bytes() == b"existing"
        assert target.with_suffix(".gguf.download").stat().st_size == 256 * 1024
        download_verified(url, target, len(payload), checksum, lambda update: None)
        assert target.read_bytes() == payload
        assert requests == [0, 256 * 1024]
    finally:
        server.shutdown()
        server.server_close()
