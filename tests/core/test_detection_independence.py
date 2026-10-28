"""Prove individual detection APIs do not import ASR, policy or each other."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("component", ["silero", "smart_turn", "policy"])
def test_detection_components_run_with_other_functions_blocked(component, tmp_path):
    code = """
import importlib.abc
import sys

component = sys.argv[1]
other = "smart_turn" if component == "silero" else "silero"
blocked = ("torch", "transformers", "qwen_asr", "fastapi", "starlette",
           "livecaptions_testapp", "livecaptions_asr.recognizer",
           "livecaptions_asr.stream", "livecaptions_asr.engines",
           "livecaptions_asr.endpoints." + other)
if component == "policy":
    blocked += ("livecaptions_asr.endpoints.smart_turn",)
else:
    blocked += ("livecaptions_asr.model_segmentation",)
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == item or fullname.startswith(item + ".") for item in blocked):
            raise AssertionError("Coupled import: " + fullname)
sys.meta_path.insert(0, Block())
import numpy as np
class Session:
    def run(self, names, inputs):
        if "state" in inputs:
            return np.array([[0.8]], dtype=np.float32), inputs["state"] + 1
        assert inputs["input_features"].shape == (1, 80, 800)
        return [np.array([[0.7]], dtype=np.float32)]
    def get_providers(self): return ["CPUExecutionProvider"]
if component == "silero":
    from livecaptions_asr import SileroVADModel
    model = SileroVADModel(Session())
    model.prepare()
    left, right = model.create_detector(), model.create_detector()
    assert left.probability(np.ones(512, dtype=np.float32)) > 0.5
    assert not right._state.any()
elif component == "smart_turn":
    from livecaptions_asr import SmartTurnEndpoint
    model = SmartTurnEndpoint(Session())
    model.prepare()
    assert model.probability(np.ones(16000, dtype=np.float32)) > 0.5
else:
    from livecaptions_asr import ModelSegmenter
    class Speech:
        frame_samples = 512
        def probability(self, pcm): return float(np.max(np.abs(pcm)) > 0.01)
        def reset(self): pass
    class Endpoint:
        context_samples = 16000
        def probability(self, pcm):
            assert len(pcm) <= self.context_samples
            return 0.9
    segmenter = ModelSegmenter(Speech(), Endpoint())
    assert segmenter.feed(np.full(16000, 0.1, dtype=np.float32), 0) == []
    assert segmenter.partial(1.0).final is False
    windows = segmenter.feed(np.zeros(12800, dtype=np.float32), 1.0)
    assert len(windows) == 1 and windows[0].boundary == "semantic_endpoint"
    assert segmenter.flush(1.8) is None and segmenter.buffered_seconds == 0
if component != "policy":
    assert model.providers == ["CPUExecutionProvider"]
assert not any(item in sys.modules for item in blocked)
print("independent", component)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, component],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"independent {component}" in result.stdout
    assert list(tmp_path.iterdir()) == []
