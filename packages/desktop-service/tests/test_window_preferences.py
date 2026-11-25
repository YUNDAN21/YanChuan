import json

import pytest
from livecaptions_service import DesktopService
from livecaptions_service.appearance import AppearanceSettings
from test_service import Runtime


def test_old_appearance_defaults_and_strict_validation(tmp_path):
    path = tmp_path / "appearance.json"
    path.write_text('{"mode":"dark"}', "utf-8")
    assert AppearanceSettings.load(path) == AppearanceSettings("dark", False)
    for value in (None, 1, "true", []):
        with pytest.raises(TypeError):
            AppearanceSettings("dark", value)


def test_tray_preference_preserves_theme_and_never_stops_recording(tmp_path):
    runtime = Runtime()
    service = DesktopService(tmp_path, runtime=runtime)
    try:
        service.configure_schedule(5, False)
        service.start("captions")
        assert runtime.entered.wait(2)
        service.set_appearance("dark")
        service.configure_window(True)
        assert service.active and not service._stop.is_set()
        service.set_appearance("light")
        assert service.configuration()["close_to_tray"] is True
        with pytest.raises(TypeError):
            service.configure_window("true")
        saved = json.loads(service.appearance_path.read_text("utf-8"))
        assert saved == {"mode": "light", "close_to_tray": True}
    finally:
        service.close()
    reopened = DesktopService(tmp_path)
    try:
        assert reopened.configuration()["close_to_tray"] is True
        reopened.configure_window(False)
        assert reopened.appearance.mode == "light"
    finally:
        reopened.close()
