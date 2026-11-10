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

## 录音中切换音源

`WasapiRecording.set_source("system" | "microphone" | "both")` 返回 `Future[str]`，完成值为实际生效音源。只在 `start()` 成功后、停止前调用；也可通过 `SessionRunner.set_source()` 转发。可切换采集器实现独立的 `SwitchableCapture` 协议，普通 PCM/文件采集器仍可只实现 `Capture`。

设备打开、启停和释放全部在 WASAPI 线程完成，识别推理不阻塞控制请求。新增设备成功启动后才提交切换，保留仍需使用的设备；新增设备失败时关闭新增流并保持原音源。音源切换继续使用开始时指定的设备 ID，未指定则使用系统默认设备。

整个会话共用同一单调时间轴和两条有界音轨，切换不重置识别流、不修改已采集 PCM；混音按每个时间位置实际覆盖的音轨数归一化。停用音轨仍定期清理过期块，停止时所有待处理切换请求均结束。模拟设备、切换失败、关闭竞态和混音回归位于 `packages/audio-capture/tests/test_wasapi_switch.py`，自动测试不打开真实设备。
