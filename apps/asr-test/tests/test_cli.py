import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from livecaptions_testapp import __main__ as cli
from livecaptions_testapp import bootstrap

from livecaptions_asr import ASRError


def test_cli_transcribe_delegates_to_core_without_frontend(monkeypatch, capsys):
    calls = []

    class Core:
        def close(self):
            calls.append("closed")

        def transcribe_file(self, path, language, context):
            calls.append((path, language, context))
            return SimpleNamespace(to_dict=lambda: {"text": "核心返回的文字"})

    monkeypatch.setattr(bootstrap, "build_recognizer", lambda config: Core())
    monkeypatch.setattr(
        sys,
        "argv",
        ["testapp", "transcribe", "sample.wav", "--language", "ja", "--context", "术语"],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["text"] == "核心返回的文字"
    assert calls == [(Path("sample.wav"), "ja", "术语"), "closed"]


def test_cli_core_error_is_reported_without_frontend(monkeypatch, capsys):
    class Core:
        def close(self):
            pass

        def transcribe_file(self, *args):
            raise ASRError("invalid_audio", "测试错误")

    monkeypatch.setattr(bootstrap, "build_recognizer", lambda config: Core())
    monkeypatch.setattr(sys, "argv", ["testapp", "transcribe", "bad.wav"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1 and "测试错误" in capsys.readouterr().err


def test_cli_help_identifies_test_application(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["testapp", "--help"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 0 and "组合测试应用" in capsys.readouterr().out
