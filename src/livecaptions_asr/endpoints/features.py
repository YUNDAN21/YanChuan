"""Whisper-compatible 16kHz/80-bin features, using NumPy only.

Implements the standard periodic Hann STFT and Slaney-normalized mel triangles.
Validated against Transformers 4.57.6's Whisper feature extractor; no runtime
dependency on Transformers, Torch, or any speech recognition model.
"""

import numpy as np

from ..domain import validated_pcm


class WhisperEndpointFeatures:
    context_samples = 128000

    def __init__(self):
        self._window = np.hanning(401)[:-1]
        # Slaney mel: linear below 1kHz, logarithmic above it.
        log_step = np.log(6.4) / 27
        max_mel = 15 + np.log(8.0) / log_step
        mels = np.linspace(0, max_mel, 82)
        frequencies = mels * (200 / 3)
        high = mels >= 15
        frequencies[high] = 1000 * np.exp((mels[high] - 15) * log_step)
        fft_frequencies = np.linspace(0, 8000, 201)[:, None]
        lower = (fft_frequencies - frequencies[:-2]) / (frequencies[1:-1] - frequencies[:-2])
        upper = (frequencies[2:] - fft_frequencies) / (frequencies[2:] - frequencies[1:-1])
        self._mel = np.maximum(0, np.minimum(lower, upper))
        self._mel *= 2 / (frequencies[2:] - frequencies[:-2])

    def __call__(self, pcm):
        pcm = validated_pcm(pcm)[-self.context_samples :]
        if len(pcm) < self.context_samples:
            pcm = np.pad(pcm, (self.context_samples - len(pcm), 0))
        pcm = (pcm - pcm.mean()) / np.sqrt(pcm.var() + 1e-7)
        padded = np.pad(pcm.astype(np.float64), (200, 200), mode="reflect")
        frames = np.lib.stride_tricks.sliding_window_view(padded, 400)[::160]
        # Match the reference's complex64 FFT buffer before power conversion.
        spectrum = np.fft.rfft(frames * self._window, axis=-1).astype(np.complex64)
        power = np.abs(spectrum, dtype=np.float64) ** 2
        log_mel = np.log10(np.maximum(1e-10, self._mel.T @ power.T)).astype(np.float32)[:, :-1]
        log_mel = np.maximum(log_mel, log_mel.max() - 8.0)
        return ((log_mel + 4.0) / 4.0).astype(np.float32)[None, ...]
