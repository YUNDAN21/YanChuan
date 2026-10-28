"""Explicit file-PCM driver for silent end-to-end verification, never used normally."""


def service_from_pcm(root, source, *, observer, runtime=None, **service_options):
    import numpy as np
    from livecaptions_service import DesktopRuntime, DesktopService

    from livecaptions_asr import AudioData
    from livecaptions_asr.audio import AudioDecoder

    chunks = list(AudioDecoder().chunks(source))
    samples = np.concatenate([chunk.audio.samples for chunk in chunks])

    class FileCapture:
        def start(self):
            pass

        def status(self):
            return {"state": "stopped", "duration_seconds": len(samples) / 16000}

        def heartbeat(self):
            pass

        def window(self, start, end):
            return AudioData.from_pcm16k(samples[round(start * 16000) : round(end * 16000)])

        def stop(self):
            pass

    runtime = runtime if runtime is not None else DesktopRuntime(root)
    device_runner = runtime.runner

    def runner(selection):
        value = device_runner(selection)
        value.capture_factory = FileCapture
        return value

    runtime.runner = runner
    return DesktopService(root, runtime=runtime, observer=observer, **service_options)
