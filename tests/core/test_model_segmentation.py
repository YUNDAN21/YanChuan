"""Endpoint policy contracts independent of model runtimes and transports."""

from dataclasses import replace

import numpy as np
import pytest
from test_api import Engine

from livecaptions_asr import ASRError, EndpointOptions, ModelSegmenter, Recognizer


class Speech:
    frame_samples = 512

    def __init__(self):
        self.resets = 0

    def probability(self, pcm):
        assert len(pcm) == self.frame_samples
        return float(np.max(np.abs(pcm)) > 0.01)

    def reset(self):
        self.resets += 1


class Endpoints:
    context_samples = 128000

    def __init__(self, probabilities):
        self.values = iter(probabilities)
        self.calls = []

    def probability(self, pcm):
        self.calls.append(pcm.copy())
        return next(self.values)


def audio(*parts):
    return np.concatenate(
        [
            np.full(round(seconds * 16000), amplitude, dtype=np.float32)
            for seconds, amplitude in parts
        ]
    )


def run(pcm, answers, *, packet=1600, options=None, partial=True):
    speech, endpoints = Speech(), Endpoints(answers)
    segmenter = ModelSegmenter(speech, endpoints, options)
    engine = Engine()
    stream = Recognizer(engine).create_stream(segmenter=segmenter)
    events = []
    for offset in range(0, len(pcm), packet):
        events.extend(stream.feed(pcm[offset : offset + packet], emit_partial=partial))
        assert stream.buffered_seconds <= segmenter.options.max_segment_seconds + 0.3
        assert segmenter.buffered_seconds <= 8.04
    events.extend(stream.finish())
    return events, engine, stream, segmenter, endpoints, speech


@pytest.mark.parametrize("packet", [17, 1599, 1600, 7493, 150000])
def test_rejected_pause_continues_same_segment_then_accepted_pause_finishes(packet):
    pcm = audio((1, 0.1), (0.8, 0), (1, 0.2), (0.8, 0), (0.5, 0.3))
    events, _, stream, segmenter, endpoints, speech = run(pcm, [0.1, 0.9], packet=packet)
    finals = [event.segment for event in events if event.kind == "final"]
    assert [item.boundary for item in finals] == ["semantic_endpoint", "end"]
    assert finals[0].start_seconds == 0 and finals[0].end_seconds > 2.8
    assert finals[1].id == 1 and finals[1].start_seconds >= finals[0].end_seconds
    assert len(endpoints.calls) == 2
    assert (
        len([event for event in events if event.kind == "partial" and event.segment.id == 0]) >= 2
    )
    assert stream.buffered_seconds == segmenter.buffered_seconds == 0
    assert speech.resets == 1


def test_eight_seconds_does_not_start_another_segment():
    events, engine, _, _, endpoints, _ = run(audio((10, 0.1), (0.8, 0)), [0.9])
    finals = [event.segment for event in events if event.kind == "final"]
    assert len(finals) == 1 and finals[0].boundary == "semantic_endpoint"
    assert finals[0].end_seconds > 10
    assert any(len(samples) > 8 * 16000 for samples, *_ in engine.calls)
    assert len(endpoints.calls) == 1 and len(endpoints.calls[0]) == 8 * 16000


def test_maximum_duration_still_bounds_continuous_speech():
    events, _, stream, _, endpoints, _ = run(audio((35, 0.1)), [])
    finals = [event.segment for event in events if event.kind == "final"]
    assert [item.boundary for item in finals] == ["max_duration", "max_duration", "end"]
    assert all(item.end_seconds - item.start_seconds <= 16.04 for item in finals)
    assert stream.duration_seconds == 35 and not endpoints.calls


def test_rejected_pause_waits_once_then_silence_timeout():
    events, _, _, _, endpoints, _ = run(audio((1, 0.1), (5, 0)), [0.1])
    finals = [event.segment for event in events if event.kind == "final"]
    assert len(finals) == 1 and finals[0].boundary == "silence_timeout"
    assert len(endpoints.calls) == 1


def test_silence_does_not_call_endpoint_or_recognizer():
    events, engine, _, _, endpoints, _ = run(np.zeros(160000, dtype=np.float32), [])
    assert not events and not engine.calls and not endpoints.calls


def test_voice_after_idle_vad_frame_overlap_keeps_full_preroll():
    # 0.3 seconds is not an integer number of 512-sample VAD frames.
    events, _, _, _, _, _ = run(audio((0.3, 0), (1, 0.1)), [], packet=17)
    final = next(event.segment for event in events if event.kind == "final")
    assert final.start_seconds < 0.1 and final.end_seconds == 1.3


def test_stop_keeps_exact_tail_without_endpoint_call_and_is_idempotent():
    pcm = np.full(33011, 0.1, dtype=np.float32)
    events, _, stream, _, endpoints, _ = run(pcm, [])
    final = next(event.segment for event in events if event.kind == "final")
    assert final.end_seconds == pytest.approx(len(pcm) / 16000, abs=0.0001)
    assert not endpoints.calls and list(stream.finish()) == []


def test_cancel_releases_model_buffers_without_new_inference():
    speech, endpoints = Speech(), Endpoints([])
    segmenter = ModelSegmenter(speech, endpoints)
    stream = Recognizer(Engine()).create_stream(segmenter=segmenter)
    list(stream.feed(np.full(32000, 0.1)))
    stream.cancel()
    assert stream.closed and stream.buffered_seconds == segmenter.buffered_seconds == 0
    assert not endpoints.calls and speech.resets == 1


def test_failed_endpoint_releases_stream_and_recurrent_state():
    speech, endpoints = Speech(), Endpoints([float("nan")])
    segmenter = ModelSegmenter(speech, endpoints)
    stream = Recognizer(Engine()).create_stream(segmenter=segmenter)
    with pytest.raises(ASRError, match="概率"):
        list(stream.feed(audio((1, 0.1), (0.8, 0))))
    assert stream.closed and stream.buffered_seconds == segmenter.buffered_seconds == 0
    assert speech.resets == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"max_wait_seconds": 0.3},
        {"endpoint_threshold": 1.1},
        {"max_segment_seconds": 0.01},
    ],
)
def test_invalid_policy_rejected(changes):
    with pytest.raises(ASRError):
        replace(EndpointOptions(), **changes)


def test_endpoint_controls_context_budget_not_policy():
    endpoint = Endpoints([0.9])
    endpoint.context_samples = 32000
    segmenter = ModelSegmenter(Speech(), endpoint)
    stream = Recognizer(Engine()).create_stream(segmenter=segmenter)
    list(stream.feed(audio((4, 0.1), (0.8, 0))))
    assert segmenter.context_samples == 32000 and len(endpoint.calls[0]) == 32000
    assert segmenter.buffered_seconds <= 2.04
    stream.cancel()


def test_preroll_is_validated_against_injected_model_context():
    endpoint = Endpoints([])
    endpoint.context_samples = 1600
    with pytest.raises(ASRError):
        ModelSegmenter(Speech(), endpoint)


def test_completed_endpoint_flushes_sentence_even_without_punctuation():
    class NoPunctuation(Engine):
        def recognize(self, audio, language, context):
            return replace(super().recognize(audio, language, context), text="finished")

    segmenter = ModelSegmenter(Speech(), Endpoints([0.9]))
    stream = Recognizer(NoPunctuation()).create_stream(segmenter=segmenter)
    events = list(stream.feed(audio((1, 0.1), (0.8, 0))))
    sentence = next(event.sentence for event in events if event.kind == "sentence")
    assert sentence.text == "finished" and sentence.boundary == "semantic_endpoint"
    stream.cancel()
