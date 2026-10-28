# 核心 API 组合测试应用

这里用于验证主项目 `livecaptions_asr`。保留采集编排、归档、文件任务、CLI、HTTP 与 OpenAPI 文档；设备适配器与混音位于 `packages/audio-capture/`，与新桌面后端共用。此应用没有网页前端。核心实现不放在这里，也不反向依赖本应用。

## 安装和运行

在仓库根目录执行：

```powershell
uv sync --project apps/asr-test --all-extras
uv run --project apps/asr-test --no-sync python scripts/download_endpoint_models.py
uv run --project apps/asr-test --no-sync python -m livecaptions_testapp serve
```

本应用的依赖只装到 `apps/asr-test/.venv`，通过可编辑路径依赖使用根目录识别库。识别使用原生 Vulkan 与独立 GGUF，无 PyTorch extra；已有模型缓存和 `data/` 不移动。命令保持在仓库根目录运行，模型、录音和上传目录默认相对当前工作目录。

通过 `http://127.0.0.1:8765/docs` 查看接口。根地址返回服务信息。可用 `LIVECAPTIONS_` 前缀环境变量或仓库根目录 `.env` 配置，字段见本应用 `config.py`；核心 API 不读取应用配置。

## 保留的功能

- WASAPI 麦克风、系统音频、双来源混音及设备选择。
- `Recognizer.create_stream()` 驱动的增量字幕，当前句修订、确认段与句子组合。
- 默认模型分段：Silero VAD 给出停顿候选，Smart Turn 确认是否结束当前段；拒绝后继续同一段，取消原先 8 秒正常强切。0.6 秒候选停顿、16 秒段长与 2 秒静音等待兜底均可配置。
- 按需保存完整音频：默认 30 秒 PCM 分卷，另导出句子 WAV 和索引，支持 WAV/RF64 流式取回与 Range。
- 长文件后台任务：逐段识别、进度、取消、排队、临时文件清理；已保存的字幕会话可重新转写。
- 默认不设录音、上传总时长或大小上限；原始采集使用短缓冲，失联后停录。

字幕应用层只负责将 PCM 交给核心，并将 `partial` / `final` / `sentence` 事件适配为快照与归档。采集线程、心跳和持久化策略不进入核心库。

## 无前端操作

```powershell
# 真实 GPU / Python 环境诊断
uv run --project apps/asr-test --no-sync python -m livecaptions_testapp doctor

# CLI 分段识别文件，不播放音频
uv run --project apps/asr-test --no-sync python -m livecaptions_testapp transcribe data/samples/asr_zh.wav --language zh

# 本应用集成测试，不打开设备或播放样本
uv run --project apps/asr-test --no-sync pytest apps/asr-test/tests -q
```

## HTTP 测试接口

| 接口 | 用途 |
| --- | --- |
| `GET /health`、`GET /v1/models` | 模型状态与实际设备 |
| `GET /v1/audio/devices` | 枚举麦克风和系统回录设备 |
| `POST /v1/captions` | 启动采集与增量字幕，支持 `save_audio` |
| `GET /v1/captions/{id}` | 文本、句子组、进度与心跳 |
| `POST /v1/captions/{id}/stop`、`cancel` | 收尾或取消 |
| `GET /v1/captions/{id}/audio`、`manifest` | 完整音频与索引 |
| `POST /v1/transcription-jobs` | 提交长文件分段任务 |
| `POST /v1/transcription-jobs/from-session` | 重新转写已保存会话 |
| `GET /v1/transcription-jobs/{id}` | 进度与结果 |
| `POST /v1/transcription-jobs/{id}/cancel` | 取消任务 |

原始数据在 `data/recordings/` 和 `data/uploads/`，历史报告仍在 `data/verification/`。取消会话不删除已保存的音频。

## 无声测试规则

自动测试只使用文件或内存 PCM，禁止自动播放。`scripts/verify_live.py` 与 `scripts/verify_long_audio.py` 是可选的播放式设备测试，默认拒绝运行，只有显式传入 `--play-audio` 才会启用；当前夜间不执行。公开样本由根目录 `scripts/verify_library.py` 获取，设备脚本读取已有 `data/samples/`。

实时处理仍为分段重复推理，不是模型原生流式。两个检测模型强制 CPU，Qwen 的设备选择独立。模型在采集前准备，找不到文件会明确返回错误，不静默回退；`LIVECAPTIONS_LIVE_SEGMENTATION=energy` 可显式选择基础能量策略。模型会话由应用复用，检测状态每个会话独立。双模型三语与小时级稳定性结果见 ../../docs/native-vulkan-migration.md；噪声专项分段评测尚未开展。
