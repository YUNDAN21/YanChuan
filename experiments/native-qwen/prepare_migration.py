"""Fetch public, revision-pinned evaluation inputs. No playback or private credentials."""

import concurrent.futures
import hashlib
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work/native-migration"


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def fetch(item):
    path = ROOT / item["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size == item["bytes"]:
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() == item["sha256"]:
                return
    partial = path.with_suffix(path.suffix + ".download")
    subprocess.run(
        [
            "curl.exe",
            "--http1.1",
            "--fail",
            "--location",
            "--retry",
            "4",
            "--retry-all-errors",
            "--speed-time",
            "45",
            "--speed-limit",
            "1024",
            "--max-time",
            "1800",
            "--silent",
            "--show-error",
            "--continue-at",
            "-",
            "--output",
            str(partial),
            item["url"] + "?download=true",
        ],
        check=True,
    )
    with partial.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if partial.stat().st_size != item["bytes"] or digest != item["sha256"]:
        raise ValueError("Download verification failed: " + item["path"])
    partial.replace(path)
    print("verified " + item["path"], flush=True)


def resolve_sources():
    WORK.mkdir(parents=True, exist_ok=True)
    items = []
    for size in ("0.6", "1.7"):
        repo = f"cstr/qwen3-asr-{size}b-GGUF"
        revision = {
            "0.6": "58bd3202f835f46b24b17142cb503b0860c737a5",
            "1.7": "674df5d44b50a63e7102a18895ed20e3f91de301",
        }[size]
        tree = get_json(f"https://huggingface.co/api/models/{repo}/tree/{revision}")
        name = f"qwen3-asr-{size}b-q8_0.gguf"
        file = next(f for f in tree if f["path"] == name)
        items.append(
            {
                "kind": "native",
                "size": size,
                "repository": repo,
                "revision": revision,
                "path": "models/native/" + name,
                "bytes": file["size"],
                "sha256": file["lfs"]["oid"],
                "url": f"https://huggingface.co/{repo}/resolve/{revision}/{name}",
            }
        )
    revision = "168de341b3db6859a9bac1c50a2ef5e3b47647e0"
    for language, config in (("zh", "cmn_hans_cn"), ("en", "en_us"), ("ja", "ja_jp")):
        tree = get_json(
            f"https://huggingface.co/api/datasets/google/fleurs/tree/{revision}/{config}/test"
        )
        file = next(f for f in tree if f["path"].endswith(".parquet"))
        items.append(
            {
                "kind": "corpus",
                "language": language,
                "revision": revision,
                "repository": "google/fleurs",
                "license": "CC-BY-4.0",
                "path": f"work/native-migration/fleurs/{language}.parquet",
                "bytes": file["size"],
                "sha256": file["lfs"]["oid"],
                "url": f"https://huggingface.co/datasets/google/fleurs/resolve/{revision}/{file['path']}",
            }
        )
    (WORK / "sources.json").write_text(json.dumps(items, indent=2), "utf-8")
    return items


def main():
    manifest = WORK / "sources.json"
    items = json.loads(manifest.read_text("utf-8")) if manifest.exists() else resolve_sources()
    items = [item for item in items if item["kind"] != "reference"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, items))
    import pyarrow.parquet as pq

    corpus = []
    for language in ("zh", "en", "ja"):
        table = pq.ParquetFile(WORK / f"fleurs/{language}.parquet")
        duration, count = 0, 0
        for batch in table.iter_batches(batch_size=16):
            for row in batch.to_pylist():
                if duration >= 600 and count >= 30:
                    break
                audio = row["audio"]["bytes"]
                path = ROOT / f"data/samples/fleurs/{language}/{count:04d}.wav"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(audio)
                seconds = row["num_samples"] / 16000
                corpus.append(
                    {
                        "language": language,
                        "id": row["id"],
                        "path": path.relative_to(ROOT).as_posix(),
                        "reference": row["raw_transcription"],
                        "seconds": seconds,
                        "sha256": hashlib.sha256(audio).hexdigest(),
                    }
                )
                duration += seconds
                count += 1
            if duration >= 600 and count >= 30:
                break
        print(f"{language}: {count} clips, {duration:.2f} seconds", flush=True)
    report = {
        "dataset": "google/fleurs",
        "revision": next(item["revision"] for item in items if item["kind"] == "corpus"),
        "split": "test",
        "license": "CC-BY-4.0",
        "selection": "First rows until >=30 clips and >=600 seconds per language",
        "samples": corpus,
    }
    (WORK / "corpus.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
