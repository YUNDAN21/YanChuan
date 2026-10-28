"""Time fresh-process model preparation and in-process reuse, without audio I/O."""

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from livecaptions_service.runtime import DesktopRuntime, InputSelection
from livecaptions_service.startup_trace import StartupTrace

from livecaptions_asr.startup import StartupTimer


def main():
    root = Path(__file__).resolve().parents[3]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = root / "data/verification" / f"startup-{stamp}"
    destination.mkdir(parents=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    runtime = DesktopRuntime(root)
    report = {
        "scope": "fresh process plus in-process reuse; no device capture or audio playback",
        "attempts": [],
    }
    for mode in ("fresh_process", "reuse"):
        trace = StartupTrace(destination / "events.jsonl", lambda event: None)
        runtime.on_progress = trace.emit
        timer = StartupTimer(trace.emit)
        segmenter = None
        try:
            with timer.phase("runtime_setup"):
                runner = runtime.runner(InputSelection())
            with timer.phase("segmentation_prepare"):
                segmenter = runner.segmenter_factory()
            runner.recognizer.prepare(on_progress=trace.emit)
            trace.finish()
        except BaseException:
            trace.finish("failed")
            raise
        finally:
            if segmenter is not None:
                segmenter.close()
        report["attempts"].append({"mode": mode, "events": trace.events})
    report["model"] = asdict(runtime._recognizer.status())
    (destination / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(destination / "report.json", flush=True)


if __name__ == "__main__":
    main()
