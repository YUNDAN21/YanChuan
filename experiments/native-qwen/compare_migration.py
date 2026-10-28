"""Acceptance calculations for paired corpus runs; no model imports."""

import argparse
import json
from pathlib import Path


def compare(reference, candidate):
    if not reference["complete"] or not candidate["complete"]:
        raise ValueError("Incomplete evaluation")
    if reference["size"] != candidate["size"]:
        raise ValueError("Model sizes differ")
    if reference["corpus_revision"] != candidate["corpus_revision"]:
        raise ValueError("Corpus revisions differ")
    ref_rows, new_rows = reference["samples"], candidate["samples"]
    if [(r["path"], r["sha256"]) for r in ref_rows] != [(r["path"], r["sha256"]) for r in new_rows]:
        raise ValueError("Evaluation inputs differ")
    languages = {}
    for language in ("zh", "en", "ja"):
        old, new = reference["languages"][language], candidate["languages"][language]
        delta = new["error_rate"] - old["error_rate"]
        languages[language] = {
            "metric": old["metric"],
            "reference_error_rate": old["error_rate"],
            "native_error_rate": new["error_rate"],
            "increase_percentage_points": delta * 100,
            "corpus_pass": new["clips"] >= 30 and new["audio_seconds"] >= 600,
            "quality_pass": delta <= 0.01,
            "hot_median_pass": new["median_seconds"] <= old["median_seconds"],
            "hot_p95_pass": new["p95_seconds"] <= old["p95_seconds"],
            "native_empty_outputs": new["empty_outputs"],
        }
    changes = []
    for old, new in zip(ref_rows, new_rows, strict=True):
        if old["text"] != new["text"]:
            changes.append(
                {
                    "path": old["path"],
                    "language": old["language"],
                    "reference_text": old["reference"],
                    "pytorch_text": old["text"],
                    "native_text": new["text"],
                    "edit_delta": new["edits"] - old["edits"],
                    "empty": new["empty"],
                }
            )
    return {
        "size": reference["size"],
        "passed": all(
            value[key]
            for value in languages.values()
            for key in ("corpus_pass", "quality_pass", "hot_median_pass", "hot_p95_pass")
        ),
        "languages": languages,
        "changed_samples": sorted(changes, key=lambda row: row["edit_delta"], reverse=True),
        "normalization": candidate["parameters"]["normalization"],
        "gpu": candidate["status"],
        "scope": "Corpus accuracy/performance only; lifecycle, stability and application gates are separate.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = compare(
        json.loads(args.reference.read_text("utf-8")),
        json.loads(args.candidate.read_text("utf-8")),
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"passed": report["passed"], "languages": report["languages"]}))
    raise SystemExit(0 if report["passed"] else 2)


if __name__ == "__main__":
    main()
