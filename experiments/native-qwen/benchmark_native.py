"""Silent persistent Vulkan probe. No production code imports or device capture."""

import argparse
import json
import os
import socket
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path


class GpuMemory:
    def __init__(self):
        self.samples = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop.is_set():
            try:
                value = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5,
                )
                self.samples.append(int(value.splitlines()[0]))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self.stop.wait(0.25)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.samples and time.monotonic() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(6)

    def report(self):
        return {
            "method": "nvidia-smi total device usage; includes other graphics applications",
            "baseline_mib": self.samples[0] if self.samples else None,
            "peak_mib": max(self.samples) if self.samples else None,
            "delta_mib": max(self.samples) - self.samples[0] if self.samples else None,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--isolated-path", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    environment = dict(os.environ)
    environment["GGML_VK_VISIBLE_DEVICES"] = "0"
    environment["GGML_VK_PIPELINE_CACHE_DEBUG"] = "0"
    environment.pop("CRISPASR_API_KEYS", None)
    if args.isolated_path:
        windows = Path(os.environ["SystemRoot"])
        environment["PATH"] = os.pathsep.join(
            (str(args.binary.resolve().parent), str(windows / "System32"), str(windows))
        )
        environment.pop("VULKAN_SDK", None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    report = {
        "silent": True,
        "capture": False,
        "model": str(args.model.resolve()),
        "backend": "CrispASR Vulkan",
        "gpu_index": 0,
        "samples": {},
        "success": False,
        "binary": str(args.binary.resolve()),
        "isolated_path": args.isolated_path,
    }
    with (
        (args.output / "server.stdout.log").open("w", encoding="utf-8") as stdout,
        (args.output / "server.stderr.log").open("w", encoding="utf-8") as stderr,
        GpuMemory() as memory,
    ):
        started = time.perf_counter()
        process = subprocess.Popen(
            [
                str(args.binary.resolve()),
                "--server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--backend",
                "qwen3",
                "--gpu-backend",
                "vulkan",
                "-dev",
                "0",
                "-m",
                str(args.model.resolve()),
                "-bo",
                "1",
                "-bs",
                "1",
                "-tp",
                "0",
                "-t",
                "4",
            ],
            stdout=stdout,
            stderr=stderr,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            deadline = time.monotonic() + 120
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Native server exited before becoming ready")
                try:
                    with opener.open(endpoint + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("Native server startup")
                time.sleep(0.1)
            report["startup_seconds"] = round(time.perf_counter() - started, 4)
            for language in ("zh", "en", "ja"):
                path = args.root / "data/samples" / ("asr_" + language + ".wav")
                with wave.open(str(path)) as audio:
                    duration = audio.getnframes() / audio.getframerate()
                timings, texts = [], []
                for run in range(4):
                    boundary = uuid.uuid4().hex
                    body = bytearray()
                    for key, value in {
                        "language": language,
                        "response_format": "json",
                        "temperature": "0",
                        "max_tokens": "512",
                    }.items():
                        body.extend(
                            (
                                f"--{boundary}\r\nContent-Disposition: form-data; "
                                f'name="{key}"\r\n\r\n{value}\r\n'
                            ).encode()
                        )
                    body.extend(
                        (
                            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                            f'filename="{path.name}"\r\nContent-Type: audio/wav\r\n\r\n'
                        ).encode()
                    )
                    body.extend(path.read_bytes())
                    body.extend(f"\r\n--{boundary}--\r\n".encode())
                    request = urllib.request.Request(
                        endpoint + "/inference",
                        data=bytes(body),
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    )
                    begin = time.perf_counter()
                    with opener.open(request, timeout=120) as response:
                        result = json.load(response)
                        seconds = time.perf_counter() - begin
                    text = result.get("text", "").strip()
                    if not text:
                        raise ValueError("Native returned empty text: " + str(result)[:200])
                    timings.append(round(seconds, 4))
                    texts.append(text)
                    print(
                        json.dumps(
                            {
                                "language": language,
                                "run": run,
                                "seconds": round(seconds, 4),
                                "text": text,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                report["samples"][language] = {
                    "duration_seconds": duration,
                    "request_seconds": timings,
                    "warm_median_seconds": statistics.median(timings[1:]),
                    "warm_rtf": statistics.median(timings[1:]) / duration,
                    "texts": texts,
                    "stable_text": len(set(texts)) == 1,
                }
                (args.output / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
                )
            report["success"] = True
        finally:
            report["gpu_memory"] = memory.report()
            process.terminate()
            process.wait(timeout=15)
            (args.output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
            )


if __name__ == "__main__":
    main()
