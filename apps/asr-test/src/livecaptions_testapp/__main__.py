import argparse
import json
import logging
from pathlib import Path

from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="LiveCaptions 核心 API 组合测试应用")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动 HTTP 服务")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    transcribe = commands.add_parser("transcribe", help="转写本地 WAV / FLAC")
    transcribe.add_argument("file", type=Path)
    transcribe.add_argument("--language", default="auto", choices=["auto", "zh", "ja", "en"])
    transcribe.add_argument("--context", default="")
    commands.add_parser("doctor", help="输出 Python / 原生运行库诊断，不加载模型")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings()
    if args.command == "doctor":
        import platform

        from livecaptions_asr.engines.native_binding import NativeBinding

        binding = NativeBinding(settings.library_path)
        result = {
            "python": platform.python_version(),
            "runtime": binding.lib.whisper_version().decode(),
            "library": str(settings.library_path.resolve()),
            "requested_device": settings.device,
            "model": str(settings.model_path.resolve()),
            "model_exists": settings.model_path.is_file(),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(
            create_app(settings), host=args.host or settings.host, port=args.port or settings.port
        )
    else:
        from livecaptions_asr.domain import ASRError

        from .bootstrap import build_recognizer

        service = build_recognizer(settings)
        try:
            result = service.transcribe_file(args.file, args.language, args.context)
        except (ASRError, OSError) as exc:
            parser.exit(1, f"{exc}\n")
        finally:
            service.close()
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
