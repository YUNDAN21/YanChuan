"""File -> Python library -> text. Never plays audio or starts a server."""

import argparse
from pathlib import Path

from livecaptions_asr import AudioDecoder, Recognizer, StreamEvent
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine


def handle(event: StreamEvent):
    if event.segment is not None:
        print(f"{event.kind} [{event.segment.id}:{event.segment.revision}] {event.segment.text}")
    elif event.sentence is not None:
        print(f"sentence [{event.sentence.id}] {event.sentence.text}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--mode", choices=["file", "stream"], default="file")
    parser.add_argument("--language", choices=["auto", "zh", "ja", "en"], default="auto")
    args = parser.parse_args()
    recognizer = Recognizer(
        NativeQwenEngine(
            NativeQwenOptions(
                Path("models/native/qwen3-asr-0.6b-q8_0.gguf"),
                Path("runtime/native/win-x64/crispasr.dll"),
            )
        )
    )
    if args.mode == "file":
        result = recognizer.transcribe_file(
            args.file,
            args.language,
            on_segment=lambda progress: print(progress.segment.text),
        )
        print(result.text)
    else:
        stream = recognizer.create_stream(args.language)
        for chunk in AudioDecoder().chunks(args.file):
            for offset in range(0, len(chunk.audio.samples), 16000):
                for event in stream.feed(chunk.audio.samples[offset : offset + 16000]):
                    handle(event)
        for event in stream.finish():
            handle(event)

    recognizer.close()


if __name__ == "__main__":
    main()
