import threading
import time

import pytest
from livecaptions_meetings import ApiProfile
from livecaptions_service import DesktopService
from livecaptions_service.config import MeetingSettings
from livecaptions_service.translation import ApiTranslator, CaptionSettings

from livecaptions_asr.results import Sentence, StreamEvent


class Translator:
    def translate(self, text, source, target):
        return {"ja": "今日の会議を始めます。", "en": "Let's start today's meeting."}.get(
            target, "今天开始会议。"
        )


def wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def ingest(service, text="今天开始会议。", sentence_id=1):
    service._project_captions(
        [
            StreamEvent(
                "sentence",
                sentence=Sentence(sentence_id, 0, 2, text, (sentence_id,), "semantic_endpoint"),
            )
        ]
    )


def test_service_translation_pairing_settings_and_original_preservation(tmp_path):
    service = DesktopService(tmp_path, translator_factory=lambda _: Translator())
    service.mode = "captions"
    service.configure_captions(CaptionSettings(target_language="ja"))
    ingest(service)
    wait(lambda: bool(service.translations))
    result = next(iter(service.translations.values()))
    assert result.source_text == "今天开始会议。" and result.text.startswith("今日")
    assert service.history.confirmed[0].text == "今天开始会议。"
    service.configure_captions(CaptionSettings(display_mode="translated", target_language="en"))
    wait(lambda: any(r.target_language == "en" for r in service.translations.values()))
    service.close()
    restored = DesktopService(tmp_path, translator_factory=lambda _: Translator())
    assert restored.caption_settings.display_mode == "translated"
    assert restored.caption_settings.target_language == "en"
    restored.close()


def test_slow_translation_never_blocks_asr_and_config_change_drops_old_results(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Slow(Translator):
        def translate(self, *args):
            entered.set()
            release.wait(3)
            return "old"

    factory = [Slow()]
    service = DesktopService(tmp_path, translator_factory=lambda _: factory[0])
    try:
        service.mode = "captions"
        service.configure_captions(CaptionSettings(target_language="ja"))
        ingest(service)
        assert entered.wait(1)
        start = time.monotonic()
        ingest(service, "继续讨论。", 2)
        assert time.monotonic() - start < 0.2
        assert len(service.history.confirmed) == 2
        factory[0] = Translator()
        service.configure_captions(CaptionSettings(target_language="en"))
        release.set()
        wait(lambda: len(service.translations) == 2)
        assert all(
            r.target_language == "en" and r.text != "old" for r in service.translations.values()
        )
    finally:
        release.set()
        service.close()


def test_translation_errors_redacted_and_new_text_continues(tmp_path):
    settings = MeetingSettings("http://localhost/v1", "m", "private-test-key")
    settings.save(tmp_path / "data/meeting-settings.json")

    class Failed(Translator):
        def translate(self, text, *args):
            if "坏" in text:
                raise ValueError("private-test-key 错误")
            return "good"

    service = DesktopService(tmp_path, translator_factory=lambda _: Failed())
    try:
        service.mode = "captions"
        service.configure_captions(CaptionSettings(target_language="en"))
        ingest(service, "坏句子。")
        wait(lambda: bool(service.translations))
        assert "private-test-key" not in service.translation_state
        ingest(service, "好句子。", 2)
        wait(lambda: any(r.text == "good" for r in service.translations.values()))
    finally:
        service.close()


def test_turning_translation_off_invalidates_pending_and_same_language_skips_api(tmp_path):
    service = DesktopService(tmp_path, translator_factory=lambda _: Translator())
    service.mode = "captions"
    service.configure_captions(CaptionSettings(enabled=False))
    ingest(service)
    assert not service.translations
    service.close()
    translator = ApiTranslator(ApiProfile("a", "local", "http://localhost/v1", "m", "key"))
    translator.client.generate = lambda _: pytest.fail("same-language input must not use network")
    assert translator.translate("今天", "Chinese", "zh") == "今天"


def test_invalid_settings_do_not_replace_previous_config(tmp_path):
    service = DesktopService(tmp_path)
    with pytest.raises(ValueError):
        service.configure_captions({"target_language": "invalid"})
    assert service.caption_settings.target_language == "zh"
    assert not (tmp_path / "data/caption-settings.json").exists()
    service.close()
