"""Summarize persisted migration evidence without loading models or accessing credentials."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from evaluate_migration import normalize
from rapidfuzz.distance import Levenshtein


def read(path):
    return json.loads(path.read_text("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    reports, package = args.reports.resolve(), args.package.resolve()
    summary = {"format": 1, "capture": False, "playback": False, "models": {}}
    errors = {
        "method": "Edit operations identify deletion/insertion cases, not semantic truth.",
        "models": {},
    }
    reference_mixed = read(reports / "mixed-reference.json")
    for size in ("0.6", "1.7"):
        native = read(reports / f"final-quality-{size}/report.json")
        old = read(reports / f"matrix/{size}-torch/report.json")
        comparison = read(reports / f"final-comparison-{size}.json")
        contracts = read(reports / f"contracts-{size}-r2/report.json")
        soak = read(
            reports
            / ("soak-0.6-r2/report.json" if size == "0.6" else "matrix/soak-1.7/report.json")
        )
        assert all(
            (
                native["complete"],
                old["complete"],
                comparison["passed"],
                contracts["complete"],
                soak["complete"],
            )
        )
        assert contracts["mixed_language"]["text"] == reference_mixed["models"][size]["text"]
        summary["models"][size] = {
            "quality_performance_pass": True,
            "comparison": comparison["languages"],
            "native_device": native["status"],
            "startup": {
                "baseline_prepare_seconds": old["prepare_seconds"],
                "native_prepare_seconds": native["prepare_seconds"],
                "baseline_first_real_seconds": old["first_real_inference_seconds"],
                "native_first_real_seconds": native["first_real_inference_seconds"],
                "scope": "Fresh processes with existing OS/driver caches; not flushed-cache cold starts",
            },
            "memory": {
                "baseline_peak_sample_rss": max(s["rss_bytes"] for s in old["samples"]),
                "native_peak_sample_rss": max(s["rss_bytes"] for s in native["samples"]),
                "baseline_gpu": old["gpu_memory"],
                "native_gpu": native["gpu_memory"],
                "released_rss_cycles": contracts["released_rss"],
            },
            "live": soak["live"],
            "file": soak["file"],
        }
        cases = []
        for previous, current in zip(old["samples"], native["samples"], strict=True):
            assert previous["sha256"] == current["sha256"]
            reference = normalize(current["reference"], current["language"])
            text = normalize(current["text"], current["language"])
            operations = Counter(op.tag for op in Levenshtein.editops(reference, text))
            words = current["text"].split()
            repeated = any(
                words[i : i + 3] == words[i + 3 : i + 6] for i in range(max(0, len(words) - 5))
            )
            if operations["delete"] or operations["insert"] or repeated or not text:
                cases.append(
                    {
                        "id": current["id"],
                        "language": current["language"],
                        "reference": current["reference"],
                        "native_text": current["text"],
                        "baseline_text": previous["text"],
                        "edit_operations": dict(operations),
                        "repeated_three_word_run": repeated,
                        "empty": not text,
                    }
                )
        errors["models"][size] = {
            "empty_outputs": sum(not row["text"] for row in native["samples"]),
            "deletion_insertion_cases": cases,
            "mixed": contracts["mixed_language"],
            "mixed_baseline": reference_mixed["models"][size],
            "mixed_identical_to_baseline": True,
        }
    for filename, key in (
        ("final-portable-r2.json", "success"),
        ("winui-final/report-r2.json", "success"),
        ("winui-final-captions/report.json", "success"),
        ("service-switch/report.json", "complete"),
        ("便携 协议验证-r2/report.json", "complete"),
    ):
        assert read(reports / filename)[key], filename
    manifest = read(package / "release-manifest.json")
    for file in manifest["files"]:
        path = package / file["path"]
        assert path.stat().st_size == file["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == file["sha256"], path
    breakdown = Counter()
    for path in package.rglob("*"):
        if path.is_file():
            relative = path.relative_to(package).as_posix()
            category = (
                "native + VC runtime"
                if relative.startswith("backend/native/")
                else "Python + ASR/audio/ONNX libraries"
                if relative.startswith("backend/python/")
                else "WinUI/.NET + application resources"
            )
            breakdown[category] += path.stat().st_size
    archive = package.parent / (package.name + ".zip")
    summary["release"] = {
        "directory": str(package),
        "zip": str(archive),
        "unpacked_bytes": sum(breakdown.values()),
        "zip_bytes": archive.stat().st_size,
        "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "breakdown_bytes": dict(breakdown),
        "manifest_hashes_verified": True,
        "credentials_scan_passed": manifest["credential_scan_passed"],
        "models_included": False,
        "configuration_included": False,
    }
    assert summary["release"]["unpacked_bytes"] <= 1024**3
    assert summary["release"]["zip_bytes"] <= 512 * 1024**2
    summary["complete"] = True
    (reports / "acceptance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), "utf-8"
    )
    (reports / "error-cases.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(summary["release"], ensure_ascii=False))


if __name__ == "__main__":
    main()
