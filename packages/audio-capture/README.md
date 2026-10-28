# LiveCaptions Audio

独立 Windows WASAPI 设备适配器，提供 `list_devices()`、`WasapiRecording` 和时间线 PCM 混音。由桌面字幕与测试应用共同使用，识别核心不依赖此包。

```python
from livecaptions_audio import WasapiRecording, list_devices

devices = list_devices()
capture = WasapiRecording("system", None, None, None, buffer_seconds=90)
# 调用方负责 start/status/heartbeat/window/stop 与单消费者调度。
```

来源支持 `system`、`microphone`、`both`；默认选系统默认设备。`window(start, end)` 输出 16 kHz 单声道 `AudioData`，只保留滚动缓冲。可注入 `AudioSink` 协议实现保存录音；采集包不知道归档格式、路径、网页或 Qt。归档实现仍由测试应用提供。

使用 `uv sync --project packages/audio-capture` 创建独立 `.venv`。此包从原测试应用提取，混音、缓冲、归档与硬件生命周期回归由 `apps/asr-test/tests/` 验证。

`livecaptions_audio.session.SessionRunner` 提供可注入的采集至识别会话调度，复用现有 `Capture` 协议、识别器和分段工厂；接受事件、状态和启动进度回调。由无界面的桌面服务调用，无 Qt、HTTP 或具体模型依赖。会话协议/收尾回归位于 `packages/desktop-service/tests/test_session.py`。
