"""Real-model long-file and long-running system capture test using public speech.

Plays the public Chinese sample twice. Saves the explicitly tested system audio
archive under data/recordings, and a verification report under data/verification.
"""

import argparse
import io
import json
import threading
from pathlib import Path
from time import monotonic

import httpx
import numpy as np
import soundfile as sf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--play-audio", action="store_true", help="显式允许播放公开测试样本")
    parser.add_argument("--seconds", type=float, default=130)
    parser.add_argument("--sample-dir", type=Path, default=Path("data/samples"))
    args = parser.parse_args()
    if not args.play_audio:
        parser.error("此设备测试会发出声音；无声验证请运行 scripts/verify_library.py。")
    import winsound

    sample = args.sample_dir / "asr_zh.wav"
    if not sample.is_file():
        raise RuntimeError("缺少公开样本，请先运行根目录 scripts/verify_library.py。")
    speech, rate = sf.read(sample, dtype="float32")
    long_file = Path("data/samples/long-public-zh.wav")
    with sf.SoundFile(long_file, "w", samplerate=rate, channels=1, subtype="PCM_16") as target:
        for _ in range(32):
            target.write(speech)
            target.write(np.zeros(round(rate * 0.4), dtype="float32"))
    report = {"sample": str(sample), "long_file_seconds": sf.info(long_file).duration}
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=30) as client:
        response = client.post(
            "/v1/captions", json={"source": "system", "language": "zh", "save_audio": True}
        )
        response.raise_for_status()
        session_id = response.json()["id"]
        report["session_id"] = session_id
        try:
            deadline = monotonic() + 120
            while True:
                state = client.get(f"/v1/captions/{session_id}").json()
                assert state["state"] != "error", state
                if state["state"] == "recording":
                    break
                assert monotonic() < deadline
                threading.Event().wait(0.2)
            assert state["limit_seconds"] is None
            started = monotonic()
            winsound.PlaySound(str(sample), winsound.SND_FILENAME)
            with long_file.open("rb") as audio:
                submitted = client.post(
                    "/v1/transcription-jobs",
                    files={"file": (long_file.name, audio)},
                    data={"language": "zh"},
                )
            submitted.raise_for_status()
            job_id = submitted.json()["id"]
            second_played = False
            measurements = []
            while monotonic() - started < args.seconds:
                elapsed = monotonic() - started
                state = client.get(f"/v1/captions/{session_id}").json()
                assert state["state"] == "recording", state
                measurements.append(
                    {
                        "elapsed": round(elapsed, 2),
                        "buffered_blocks": state["buffered_blocks"],
                        "processed_seconds": state["processed_seconds"],
                    }
                )
                if not second_played and elapsed >= max(5, args.seconds - 8):
                    winsound.PlaySound(str(sample), winsound.SND_FILENAME)
                    second_played = True
                threading.Event().wait(0.5)
            report["measurements"] = measurements
            client.post(f"/v1/captions/{session_id}/stop").raise_for_status()
            deadline = monotonic() + 45
            while True:
                state = client.get(f"/v1/captions/{session_id}").json()
                assert state["state"] != "error", state
                if state["finished"]:
                    break
                assert monotonic() < deadline
                threading.Event().wait(0.2)
            report["live"] = state
            assert state["audio_available"] and "交易" in state["text"]
            manifest = client.get(f"/v1/captions/{session_id}/manifest")
            manifest.raise_for_status()
            report["manifest"] = manifest.json()
            audio = client.get(f"/v1/captions/{session_id}/audio")
            audio.raise_for_status()
            recorded, recorded_rate = sf.read(io.BytesIO(audio.content))
            assert abs(len(recorded) / recorded_rate - state["duration_seconds"]) < 0.03
            assert (
                client.get(
                    f"/v1/captions/{session_id}/audio", headers={"Range": "bytes=0-99"}
                ).status_code
                == 206
            )
            deadline = monotonic() + 90
            while True:
                job = client.get(f"/v1/transcription-jobs/{job_id}").json()
                if job["state"] == "completed":
                    break
                assert job["state"] != "error", job
                assert monotonic() < deadline
                threading.Event().wait(0.3)
            report["file_job"] = job
            assert job["result"]["duration_seconds"] > 120 and job["chunks_completed"] > 1
            assert "交易" in job["result"]["text"]
        finally:
            client.post(f"/v1/captions/{session_id}/cancel")
    output = Path("data/verification/pipeline-long-audio.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(output),
                "capture_seconds": state["duration_seconds"],
                "source_parts": len(report["manifest"]["source_parts"]),
                "semantic_sections": len(report["manifest"]["semantic_sections"]),
                "file_seconds": job["result"]["duration_seconds"],
                "file_chunks": job["chunks_completed"],
            },
            ensure_ascii=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
