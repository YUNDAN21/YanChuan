"""Appearance preferences, independent of summary credentials and Qt."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppearanceSettings:
    mode: str = "light"

    def __post_init__(self):
        if self.mode not in {"light", "dark"}:
            raise ValueError("外观模式须为 light 或 dark")

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8"))["mode"])

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"mode": self.mode}), encoding="utf-8")
        temporary.replace(path)
