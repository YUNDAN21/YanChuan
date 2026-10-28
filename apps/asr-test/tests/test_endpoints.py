"""Composition uses one loaded model bundle and fresh segmentation per stream."""

import pytest
from livecaptions_testapp.config import Settings
from livecaptions_testapp.endpoints import SegmenterFactory

from livecaptions_asr import ASRError, EnergySegmenter
from livecaptions_asr.endpoints.silero import SileroVADModel
from livecaptions_asr.endpoints.smart_turn import SmartTurnEndpoint


def test_model_factory_prepares_once_and_creates_independent_segmenters(monkeypatch):
    calls = []

    class Detector:
        frame_samples = 512

    class Speech:
        def prepare(self):
            calls.append("speech_prepare")

        def create_detector(self):
            calls.append("create")
            return Detector()

    class Endpoint:
        context_samples = 128000

        def prepare(self):
            calls.append("endpoint_prepare")

    def speech_load(*args, **kwargs):
        calls.append("speech_load")
        return Speech()

    def endpoint_load(*args, **kwargs):
        calls.append("endpoint_load")
        return Endpoint()

    monkeypatch.setattr(SileroVADModel, "from_path", speech_load)
    monkeypatch.setattr(SmartTurnEndpoint, "from_path", endpoint_load)
    factory = SegmenterFactory(Settings(_env_file=None))
    left, right = factory(), factory()
    assert left is not right and left.speech is not right.speech
    assert left.endpoint is right.endpoint
    assert calls == [
        "speech_load",
        "endpoint_load",
        "speech_prepare",
        "endpoint_prepare",
        "create",
        "create",
    ]
    assert left.options.max_segment_seconds == 16


def test_model_factory_does_not_silently_fall_back_when_files_missing(tmp_path):
    factory = SegmenterFactory(Settings(_env_file=None, vad_model_path=tmp_path / "missing.onnx"))
    with pytest.raises(ASRError) as error:
        factory()
    assert error.value.code == "endpoint_model_missing" and factory.models is None


def test_explicit_energy_policy_does_not_load_models(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Should not load model")

    monkeypatch.setattr(SileroVADModel, "from_path", fail)
    monkeypatch.setattr(SmartTurnEndpoint, "from_path", fail)
    segmenter = SegmenterFactory(Settings(_env_file=None, live_segmentation="energy"))()
    assert isinstance(segmenter, EnergySegmenter)
