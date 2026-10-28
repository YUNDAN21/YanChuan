"""Probe supported summary protocols on the configured gateway with one synthetic sentence."""

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from livecaptions_meetings import SummaryError, TranscriptEntry, create_summary_client
from livecaptions_service.config import MeetingSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = MeetingSettings.load(args.root / "data/meeting-settings.json").summary_settings()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or args.root / "data/verification" / f"summary-protocols-{stamp}"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for protocol in ["chat_completions", "responses", "anthropic"]:
        started = monotonic()
        configured = replace(
            settings,
            protocol=protocol,
            timeout_seconds=30,
            system_prompt="Only output OK. This is a connectivity test.",
        )
        result = {"protocol": protocol, "model": settings.model}
        try:
            text = create_summary_client(configured).summarize(
                "Connectivity test", [TranscriptEntry(1, "probe", 0, 1, "Connectivity test.")]
            )
            result.update(success=True, text=text)
        except SummaryError as exc:
            result.update(success=False, error=str(exc))
        result["seconds"] = round(monotonic() - started, 3)
        results.append(result)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    report = {"base_url": settings.base_url, "results": results, "original_config_modified": False}
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(str(output / "report.json"))


if __name__ == "__main__":
    main()
