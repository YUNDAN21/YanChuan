"""Public Qwen samples used by the silent library verification."""

from pathlib import Path
from urllib.request import urlopen


def public_sample(language: str) -> Path:
    path = Path(f"data/samples/asr_{language}.wav").resolve()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(
            f"https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_{language}.wav",
            timeout=60,
        ) as response:
            path.write_bytes(response.read())
    return path
