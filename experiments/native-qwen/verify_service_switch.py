"""Real model switching and failure recovery through the public headless service API."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from livecaptions_service import DesktopService, InputSelection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    events, runs = [], []
    service = DesktopService(output, observer=events.append)
    report = {"complete": False, "capture": False, "playback": False, "runs": runs}
    previous = None
    try:
        service.configure_schedule(5, False)
        for size in ("0.6", "1.7", "0.6"):
            service.configure_models(f"qwen-{size}b", directory=str(root / "models"))
            if previous is not None:
                assert previous.status().state == "unloaded"
            for repeat in range(2):
                service.start(
                    "file",
                    title=f"Switch {size} {repeat}",
                    selection=InputSelection(language="zh"),
                    file=root / "data/samples/asr_zh.wav",
                )
                assert service.wait(120)
                snapshot = service.snapshot()
                assert snapshot["state"] == "ended" and snapshot["meeting"]["entries"]
                runs.append(
                    {
                        "size": size,
                        "repeat": repeat,
                        "status": asdict(service.runtime.recognizer().status()),
                    }
                )
                assert runs[-1]["status"]["load_count"] == 1
            previous = service.runtime.recognizer()
        saved = snapshot["meeting"]["directory"]
        # A missing runtime is a real load failure; already persisted meetings remain readable.
        service.runtime.close()
        service.runtime.library_path = output / "missing.dll"
        service.start("file", file=root / "data/samples/asr_zh.wav", title="Missing runtime")
        assert service.wait(30)
        assert service.snapshot()["state"] == "error"
        service.open_meeting(saved)
        assert service.snapshot()["meeting"]["entries"]
        report["history_survives_load_failure"] = True
        report["complete"] = True
    finally:
        service.close()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )


if __name__ == "__main__":
    main()
