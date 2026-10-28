"""Application-owned lazy model lifetime; each caption session gets fresh state."""

import threading

from livecaptions_asr import EndpointOptions, EnergySegmenter, ModelSegmenter

from .config import Settings


class SegmenterFactory:
    def __init__(self, config: Settings):
        self.config = config
        self.models = None
        self._lock = threading.Lock()

    def __call__(self):
        config = self.config
        if config.live_segmentation == "energy":
            return EnergySegmenter(
                config.live_energy_threshold,
                config.live_silence_seconds,
                config.live_segment_seconds,
                config.live_update_seconds,
            )
        from livecaptions_asr.endpoints.silero import SileroVADModel
        from livecaptions_asr.endpoints.smart_turn import SmartTurnEndpoint

        options = EndpointOptions(
            speech_threshold=config.speech_threshold,
            endpoint_threshold=config.endpoint_threshold,
            silence_seconds=config.live_silence_seconds,
            max_wait_seconds=config.live_max_wait_seconds,
            max_segment_seconds=config.live_segment_seconds,
            update_seconds=config.live_update_seconds,
        )
        with self._lock:
            if self.models is None:
                speech = SileroVADModel.from_path(config.vad_model_path)
                endpoint = SmartTurnEndpoint.from_path(config.endpoint_model_path)
                speech.prepare()
                endpoint.prepare()
                self.models = (speech, endpoint)
        speech, endpoint = self.models
        return ModelSegmenter(speech.create_detector(), endpoint, options)
