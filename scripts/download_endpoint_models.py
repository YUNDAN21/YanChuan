"""Download exact upstream model revisions; preserve and verify existing assets."""

from pathlib import Path
from urllib.request import urlopen

from livecaptions_asr.endpoints.runtime import cpu_session

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "silero_vad.onnx": (
        (
            "https://raw.githubusercontent.com/snakers4/silero-vad/"
            "066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/data/silero_vad.onnx"
        ),
        2327524,
        {"input", "state", "sr"},
    ),
    "smart-turn-v3.2-cpu.onnx": (
        (
            "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/"
            "f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx"
        ),
        8679182,
        {"input_features"},
    ),
}


def main():
    directory = ROOT / "models/endpoint"
    directory.mkdir(parents=True, exist_ok=True)
    for name, (url, expected_size, inputs) in FILES.items():
        target = directory / name
        if target.is_file():
            data = target.read_bytes()
        elif (cached := ROOT / "models/endpoint-benchmark" / name).is_file():
            data = cached.read_bytes()
        else:
            with urlopen(url, timeout=60) as response:
                data = response.read(expected_size + 1)
        if len(data) != expected_size:
            raise ValueError(f"Model size mismatch: {name}; existing files left unchanged")
        temporary = target.with_suffix(".download")
        try:
            temporary.write_bytes(data)
            session = cpu_session(temporary, 1)
            if not inputs.issubset({item.name for item in session.get_inputs()}):
                raise ValueError(f"Model inputs mismatch: {name}")
            del session
            if not target.is_file():
                temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Verified {name}: {len(data)} bytes")


if __name__ == "__main__":
    main()
