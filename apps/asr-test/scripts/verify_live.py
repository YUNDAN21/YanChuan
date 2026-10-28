"""Real-time playback -> WASAPI -> incremental Qwen captions, using public samples."""

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--play-audio", action="store_true", help="显式允许播放公开测试样本")
    parser.add_argument("--languages", nargs="+", default=["zh", "en"], choices=["zh", "en"])
    parser.add_argument("--source", default="system", choices=["system", "both"])
    parser.add_argument("--sample-dir", type=Path, default=Path("data/samples"))
    args = parser.parse_args()
    if not args.play_audio:
        parser.error("此设备测试会发出声音；无声验证请运行 scripts/verify_library.py。")
    import winsound

    report = {"mode": "chunked", "native_streaming": False, "source": args.source, "samples": {}}
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=15) as client:
        for language in args.languages:
            path = args.sample_dir / f"asr_{language}.wav"
            if not path.is_file():
                raise RuntimeError("缺少公开样本，请先运行根目录 scripts/verify_library.py。")
            started = client.post(
                "/v1/captions",
                json={"source": args.source, "language": language, "save_audio": True},
            )
            started.raise_for_status()
            session_id = started.json()["id"]
            snapshots, during_playback = [], []
            deadline = monotonic() + 120
            preparation_started = monotonic()
            revision = 0
            try:
                while True:
                    response = client.get(f"/v1/captions/{session_id}")
                    response.raise_for_status()
                    state = response.json()
                    assert state["state"] != "error", state
                    if state["state"] == "recording":
                        break
                    assert monotonic() < deadline, "Model preparation timed out"
                    threading.Event().wait(0.2)
                preparation_seconds = monotonic() - preparation_started
                with ThreadPoolExecutor(max_workers=1) as pool:
                    playback = pool.submit(winsound.PlaySound, str(path), winsound.SND_FILENAME)
                    while not playback.done():
                        state = client.get(f"/v1/captions/{session_id}").json()
                        assert state["state"] != "error", state
                        if state["revision"] != revision:
                            revision = state["revision"]
                            snapshots.append(state)
                            if state["text"]:
                                during_playback.append(state)
                        client.get("/health").raise_for_status()
                        threading.Event().wait(0.2)
                    playback.result()
                client.post(f"/v1/captions/{session_id}/stop").raise_for_status()
                deadline = monotonic() + 45
                while True:
                    state = client.get(f"/v1/captions/{session_id}").json()
                    assert state["state"] != "error", state
                    if state["state"] == "stopped":
                        break
                    assert monotonic() < deadline, "Tail drain timed out"
                    threading.Event().wait(0.2)
                assert state["audio_available"]
                client.get(f"/v1/captions/{session_id}/audio").raise_for_status()
            finally:
                client.post(f"/v1/captions/{session_id}/cancel")
            result = {
                "sample": str(path),
                "preparation_seconds": round(preparation_seconds, 3),
                "updates_during_playback": len(during_playback),
                "snapshots": snapshots,
                "final": state,
            }
            report["samples"][language] = result
            output = Path(f"data/verification/pipeline-live-{args.source}.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "language": language,
                        "updates_during_playback": len(during_playback),
                        "final": state,
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
            assert during_playback, "No caption was visible while speech was playing"
            assert state["segments"] and all(item["final"] for item in state["segments"])
            if language == "zh":
                assert "交易" in state["text"], "Chinese final text did not match public sample"


if __name__ == "__main__":
    main()
