"""State isolation and trained-model preprocessing contracts for optional adapters."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from livecaptions_asr import ASRError
from livecaptions_asr.endpoints.silero import SileroSpeechDetector
from livecaptions_asr.endpoints.smart_turn import SmartTurnEndpoint


class Session:
    def __init__(self):
        self.inputs = []

    def run(self, names, inputs):
        self.inputs.append({key: value.copy() for key, value in inputs.items()})
        if "state" in inputs:
            return np.array([[0.8]], dtype=np.float32), inputs["state"] + 1
        return [np.array([[0.7]], dtype=np.float32)]


def test_shared_session_keeps_independent_vad_states():
    session = Session()
    left, right = SileroSpeechDetector(session), SileroSpeechDetector(session)
    left.probability(np.full(512, 0.1, dtype=np.float32))
    left.probability(np.full(512, 0.2, dtype=np.float32))
    right.probability(np.full(512, 0.3, dtype=np.float32))
    assert [float(item["state"].max()) for item in session.inputs] == [0, 1, 0]
    assert all(item["input"].shape == (1, 576) for item in session.inputs)
    assert np.all(session.inputs[1]["input"][0, :64] == np.float32(0.1))
    assert np.all(session.inputs[2]["input"][0, :64] == 0)
    left.reset()
    left.probability(np.ones(512, dtype=np.float32))
    assert not session.inputs[-1]["state"].any()


def test_vad_rejects_wrong_frame_length():
    with pytest.raises(ASRError):
        SileroSpeechDetector(Session()).probability(np.ones(1600))


@pytest.mark.parametrize("length", [5000, 128000, 160000])
def test_smart_turn_features_match_official_whisper_pipeline(length):
    fixtures = Path(__file__).parent / "fixtures"
    path = fixtures / "smart_turn_official_features.npz"
    metadata = json.loads(path.with_suffix(".json").read_text("utf-8"))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
    session = Session()
    endpoint = SmartTurnEndpoint(session)
    pcm = np.random.default_rng(51).normal(0, 0.1, length).astype(np.float32)
    with np.load(path) as data:
        reference = data[str(length)]
    actual = endpoint.features(pcm)
    assert actual.shape == (1, 80, 800) and actual.dtype == np.float32
    np.testing.assert_allclose(actual, reference, atol=1e-5, rtol=1e-5)
    assert endpoint.probability(pcm) == pytest.approx(0.7)
    np.testing.assert_array_equal(session.inputs[-1]["input_features"], actual)
