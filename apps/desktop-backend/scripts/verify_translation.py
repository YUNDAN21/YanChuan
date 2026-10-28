"""Translate short synthetic samples using existing API configuration; no audio or devices."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from livecaptions_service.config import MeetingSettings
from livecaptions_service.translation import ApiTranslator


def main():
    root = Path(__file__).resolve().parents[3]
    profile = MeetingSettings.load(root / "data/meeting-settings.json").profile_library().selected
    samples = [
        ("chat_completions", "今天下午三点开会。", "Chinese", "en"),
        ("responses", "We will discuss the schedule tomorrow.", "English", "zh"),
        ("anthropic", "会議は午後三時に始まります。", "Japanese", "zh"),
    ]
    results = []
    for protocol, text, source, target in samples:
        translated = ApiTranslator(replace(profile, protocol=protocol)).translate(
            text, source, target
        )
        if not translated or translated == text:
            raise RuntimeError("Synthetic translation did not return translated text")
        results.append(
            {"protocol": protocol, "original": text, "translation": translated, "target": target}
        )
    output = (
        root / "data/verification" / ("translation-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    output.mkdir(parents=True)
    (output / "report.json").write_text(
        json.dumps(
            {"success": True, "model": profile.model, "silent": True, "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output / "report.json")


if __name__ == "__main__":
    main()
