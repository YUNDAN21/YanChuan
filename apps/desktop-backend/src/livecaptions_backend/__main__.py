"""Run a trusted parent/child JSON Lines channel; no listening socket."""

import argparse
import io
import json
import logging
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def serve(input_stream, output_stream, root, *, service_factory=None):
    from livecaptions_service import DesktopService

    from .protocol import Router, json_default

    outgoing = queue.Queue()
    broken = threading.Event()

    def write():
        try:
            while (message := outgoing.get()) is not None:
                output_stream.write(
                    json.dumps(message, default=json_default, ensure_ascii=False, allow_nan=False)
                    + "\n"
                )
                output_stream.flush()
        except (OSError, ValueError):
            broken.set()

    writer = threading.Thread(target=write, name="protocol-writer", daemon=True)
    writer.start()

    def observe(event):
        outgoing.put(
            {
                "type": "event",
                "seq": event.sequence,
                "session_id": event.session_id,
                "name": event.name,
                "payload": event.payload,
            }
        )

    service = (service_factory or DesktopService)(root, observer=observe)
    router = Router(service)

    def respond(request):
        try:
            result = router.dispatch(request["method"], request.get("params") or {})
            outgoing.put({"type": "response", "id": request["id"], "result": result})
        except Exception as exc:  # noqa: BLE001 - return safe errors at the transport boundary
            outgoing.put(
                {
                    "type": "response",
                    "id": request.get("id"),
                    "error": {
                        "code": getattr(exc, "code", "request_failed"),
                        "message": service._error(exc),
                    },
                }
            )

    try:
        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="devices") as devices,
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="api-models") as model_requests,
        ):
            for line in input_stream:
                if broken.is_set():
                    break
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict) or not isinstance(request.get("method"), str):
                        raise TypeError()
                    if "id" not in request or not isinstance(request.get("params", {}), dict):
                        raise ValueError()
                except (ValueError, TypeError):
                    outgoing.put(
                        {
                            "type": "response",
                            "id": None,
                            "error": {"code": "invalid_request", "message": "无效请求"},
                        }
                    )
                    continue
                if request["method"] == "devices.list":
                    devices.submit(respond, request)
                elif request["method"] == "profiles.models":
                    model_requests.submit(respond, request)
                else:
                    respond(request)
                if request["method"] == "shutdown":
                    break
    finally:
        service.close()
        outgoing.put(None)
        writer.join(5)


def main():
    if sys.platform == "win32":
        import multiprocessing

        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            multiprocessing.set_executable(str(pythonw))
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--native-library", type=Path, help="Override the bundled native runtime DLL"
    )
    parser.add_argument("--device", default="vulkan", help="vulkan, VulkanN or cpu")
    parser.add_argument(
        "--verify-pcm", type=Path, help="Silent verification input; never opens devices"
    )
    args = parser.parse_args()
    # Preserve a dedicated pipe before redirecting even native dependency stdout to stderr.
    wire = io.TextIOWrapper(
        os.fdopen(os.dup(sys.stdout.fileno()), "wb"),
        encoding="utf-8",
        newline="\n",
        write_through=True,
    )
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:

        def factory(root, *, observer):
            from livecaptions_service import DesktopRuntime, DesktopService

            runtime = DesktopRuntime(root, library_path=args.native_library, device=args.device)
            if args.verify_pcm is not None:
                from .verification import service_from_pcm

                return service_from_pcm(root, args.verify_pcm, observer=observer, runtime=runtime)
            return DesktopService(root, runtime=runtime, observer=observer)

        serve(sys.stdin, wire, args.root.resolve(), service_factory=factory)
    finally:
        wire.close()


if __name__ == "__main__":
    main()
