"""Download exact upstream model revisions; preserve and verify existing assets."""

import hashlib
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "silero_vad.onnx": (
        (
            "https://raw.githubusercontent.com/snakers4/silero-vad/"
            "066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/data/silero_vad.onnx"
        ),
        "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
    ),
    "smart-turn-v3.2-cpu.onnx": (
        (
            "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/"
            "f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx"
        ),
        "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f",
    ),
}


def main():
    directory = ROOT / "models/endpoint"
    directory.mkdir(parents=True, exist_ok=True)
    for name, (url, expected_hash) in FILES.items():
        target = directory / name
        if target.is_file():
            data = target.read_bytes()
        elif (cached := ROOT / "models/endpoint-benchmark" / name).is_file():
            data = cached.read_bytes()
        else:
            with urlopen(url, timeout=60) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError(f"Model hash mismatch: {name}; existing files left unchanged")
        if not target.is_file():
            target.write_bytes(data)
        print(f"Verified {name}: {len(data)} bytes")


if __name__ == "__main__":
    main()
