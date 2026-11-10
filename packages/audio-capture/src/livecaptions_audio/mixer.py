import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import resample_poly


@dataclass(frozen=True)
class CapturedBlock:
    offset_seconds: float
    sample_rate: int
    samples: np.ndarray


def mix_samples(
    tracks: list[list[CapturedBlock]],
    duration_seconds: float,
    start_seconds: float = 0,
    *,
    active_gain: bool = False,
) -> np.ndarray:
    """Place blocks on a shared monotonic timeline; silence retains its duration.

    Timing is callback-based, not sample-accurate hardware clock synchronization.
    By default each track has a constant gain. With active_gain, normalize only
    the sources covering each sample, including silent blocks, so a disabled
    input does not attenuate the remaining input after a source switch.
    """
    length = max(1, round(duration_seconds * 16000))
    mixed = np.zeros(length, dtype=np.float32)
    gain = 1 / max(1, len(tracks))
    coverage = np.zeros(length, dtype=np.uint8) if active_gain else None
    for track in tracks:
        present = np.zeros(length, dtype=bool) if active_gain else None
        for block in track:
            if (
                block.offset_seconds >= start_seconds + duration_seconds
                or block.offset_seconds + len(block.samples) / block.sample_rate <= start_seconds
            ):
                continue
            factor = math.gcd(block.sample_rate, 16000)
            samples = resample_poly(block.samples, 16000 // factor, block.sample_rate // factor)
            offset = round((block.offset_seconds - start_seconds) * 16000)
            if offset < 0:
                samples = samples[-offset:]
            start = max(0, offset)
            count = min(len(samples), length - start)
            if count > 0:
                mixed[start : start + count] += samples[:count] * (1 if active_gain else gain)
                if present is not None:
                    present[start : start + count] = True
        if coverage is not None:
            coverage += present
    if coverage is not None:
        mixed /= np.maximum(coverage, 1)
    return np.clip(mixed, -1, 1)
