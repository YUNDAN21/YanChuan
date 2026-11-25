"""Appearance preferences, independent of summary credentials and Qt."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppearanceSettings:
    mode: str = "light"
    close_to_tray: bool = False

    def __post_init__(self):
        if self.mode not in {"light", "dark"}:
            raise ValueError("外观模式须为 light 或 dark")
        if not isinstance(self.close_to_tray, bool):
            raise TypeError("关闭后保留在托盘须为布尔值")

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["mode"], data.get("close_to_tray", False))

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self)), encoding="utf-8")
        temporary.replace(path)
