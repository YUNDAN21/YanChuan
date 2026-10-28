"""Real incremental ASR and endpoint models from file PCM, without opening devices."""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from livecaptions_service import DesktopRuntime, DesktopService, InputSelection

from livecaptions_asr import AudioData
from livecaptions_asr.audio import AudioDecoder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    source = args.file or args.root / "data/samples/asr_zh.wav"
    chunks = list(AudioDecoder().chunks(source))
    import numpy as np

    samples = np.concatenate([chunk.audio.samples for chunk in chunks])

    class FileCapture:
        def start(self):
            pass

        def status(self):
            return {"state": "stopped", "duration_seconds": len(samples) / 16000}

        def heartbeat(self):
            pass

        def window(self, start, end):
            return AudioData.from_pcm16k(samples[round(start * 16000) : round(end * 16000)])

        def stop(self):
            pass

    runtime = DesktopRuntime(args.root)
    device_runner = runtime.runner

    def runner(selection):
        value = device_runner(selection)
        value.capture_factory = FileCapture
        return value

    runtime.runner = runner
    events = []
    service = DesktopService(args.root, runtime=runtime, observer=events.append)
    try:
        for _ in range(2):
            service.start("captions", selection=InputSelection(language="zh"))
            if not service.wait(300) or service.state != "ended":
                raise RuntimeError("Incremental session did not complete")
        snapshot = service.snapshot()
        assert len(snapshot["captions"]) >= 2
        status = asdict(runtime.recognizer().status())
        assert status["load_count"] == 1
        output = (
            args.root
            / "data/verification"
            / ("desktop-service-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
        )
        output.mkdir(parents=True)
        report = {
            "success": True,
            "silent": True,
            "device_opened": False,
            "sessions": 2,
            "captions": snapshot["captions"],
            "model": status,
            "event_count": len(events),
        }
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(output / "report.json")
    finally:
        service.close()


if __name__ == "__main__":
    main()
