# 功能边界与独立 API

2026-10-23 解耦复核完成。各能力通过 PCM、概率、窗口及结果类型协作，具体模型组合只放在应用层。

| 能力 | 独立入口 | 是否需要其他具体模型或网页 |
| --- | --- | --- |
| 语音识别 | `Recognizer.recognize` / `transcribe_file` | 不需要 VAD、Smart Turn、HTTP 或设备 |
| 人声检测 | `SileroVADModel.from_path` → `create_detector().probability` | 不需要识别、端点模型或分段器 |
| 发言结束判断 | `SmartTurnEndpoint.from_path` → `probability` | 不需要 VAD、识别或分段器 |
| 分段策略 | `ModelSegmenter.feed` / `partial` / `flush` | 仅依赖检测协议，不加载具体模型或执行识别 |
| 增量识别组合 | `Recognizer.create_stream(segmenter=...)` | 通过协议组合分段与识别，不依赖网页或录音设备 |
| 设备与 PCM 混音 | `packages/audio-capture/` | 独立 WASAPI 适配器；可注入归档协议，无 Qt/HTTP/应用依赖 |
| 录音归档、HTTP 与 CLI 测试 | `apps/asr-test/` | 外层选择模型、配置、生命周期并消费事件，无网页 |
| 会议与置顶字幕界面 | `apps/desktop-winui/` | C# 原生呈现，通过客户端接口调用后端，不实现识别业务 |
| 启动计时 | `StartupEvent` / `StartupTimer`，`NativeQwenEngine(options, on_progress=...)`、`Recognizer.prepare(on_progress=...)` | 核心只输出观察事件，桌面服务写计时日志，WinUI 显示 |
| 短字幕拆分 | `SubtitleSplitter.split` → `SubtitleCue` | 只使用文本、句读/短语规则及可选宽度测量，不加载模型或改变识别/端点分段 |
| 会议持久化 | `livecaptions_meetings.MeetingStore` | SQLite/Markdown 保存和时间戳，无 Qt、ASR、模型或设备依赖 |
| 分时段 AI 总结 | `SummaryWorker` / `SummaryClient` / `ChatSummaryClient` | 单独后台调度、独立兼容 API 适配器；通过检查点只提交新增转写 |
| 桌面服务 API | `packages/desktop-service/` | 无 Qt、HTTP、WinUI 或进程协议，可直接调用和注入运行时 |
| 桌面进程接口 | `apps/desktop-backend/` | 薄 JSON Lines 路由，依赖服务库，不导入前端或 HTTP 应用 |

通用采集至识别会话 `SessionRunner` 位于 `livecaptions_audio.session`。桌面固定模型装配位于服务层 `runtime.py`，一次只运行一个转写任务，同进程复用模型；测试 HTTP 应用仍独立装配，不相互导入。会议保存、AI 请求、定时器和进程通信不进入 ASR 核心。详细接口见 [桌面 API](desktop-api.md)。

## 模型分别调用

```python
from pathlib import Path
from livecaptions_asr import SileroVADModel, SmartTurnEndpoint

# 仅用人声检测时，只创建这个模型。
vad_model = SileroVADModel.from_path(Path("models/endpoint/silero_vad.onnx"))
vad = vad_model.create_detector()
speech_probability = vad.probability(pcm_frame_512)  # 16 kHz、单声道 float32
vad.reset()

# 仅用端点判断时，只创建这个模型。
endpoint = SmartTurnEndpoint.from_path(Path("models/endpoint/smart-turn-v3.2-cpu.onnx"))
end_probability = endpoint.probability(pcm_audio)
```

没有两模型绑定加载的核心入口；旧 `OnnxEndpointModels` 已移除，避免调用 VAD 时被迫加载端点模型。上面两部分可以分别用于其他软件。

## 可替换的策略

```python
from livecaptions_asr import EndpointOptions, ModelSegmenter

segmenter = ModelSegmenter(
    vad_model.create_detector(),
    endpoint,
    EndpointOptions(max_segment_seconds=16, max_wait_seconds=2),
)
windows = segmenter.feed(pcm_chunk, offset_seconds)
# Window 只是时间边界；此处没有识别或磁盘写入。
partial = segmenter.partial(current_end_seconds)
tail = segmenter.flush(current_end_seconds)
```

直接使用策略时，调用方负责保存待识别音频并消费窗口；也可交给 `PCMStream` 自动管理有界识别缓冲。停止后不得继续 feed；取消时可直接 `close()` 清空检测音频和 VAD 状态。

- `SpeechDetector`、`EndpointDetector` 位于独立 `endpoints/protocols.py`，不依赖运行时。
- 端点接口用 `context_samples` 声明上下文预算；策略不再写死 8 秒。Smart Turn 声明 128000 样本，替换成其他实现可使用不同预算。
- Silero 适配器不导入 Smart Turn、特征模块、分段器或识别流水线；Smart Turn 也不导入 Silero 或识别流水线。
- Smart Turn 的 `WhisperEndpointFeatures` 是纯 NumPy 实现，不依赖 Transformers/Torch。可选 `endpoint` extra 仅额外安装 ONNX Runtime。
- 根包公开导出按功能延迟导入；`from livecaptions_asr import SileroVADModel` 不会顺带导入识别流水线或其他模型。
- 测试应用的 `SegmenterFactory` 才组合两模型并复用会话；每流 VAD 状态独立。核心不读取 `.env`、下载模型或启动线程/设备。

## 验证证据

- 核心 70 项、测试应用 43 项通过；Ruff、两个锁文件和 wheel 检查通过。wheel 的 `endpoint` 依赖仅 ONNX Runtime，删除绑定加载模块，无应用/HTTP/设备/网页。
- 新子进程屏蔽识别引擎、流、策略、另一模型、Torch、Transformers 与测试应用后，分别运行 VAD/端点 API。另一个隔离用例屏蔽两模型与识别流水线，直接运行注入检测器的分段策略。
- 2 秒上下文的替代端点实测策略只保留并传递相应窗口，证明策略未绑定 Smart Turn 的 8 秒设置。
- 短、8 秒和超长 PCM 的 NumPy 特征与官方 Whisper 预处理在 `1e-5` 容差内一致。
- `scripts/verify_detection_apis.py` 在独立子进程加载真实权重，在禁止上述无关模块导入的情况下运行，两者均为 CPU provider，禁止模块导入列表为空。报告：`data/verification/independent-detection-20261023T042525Z.json`。
- `scripts/verify_model_segmentation.py` 重新组合后完成真实中英文识别；英文保持 14.608 秒同段，两模型在 CPU、Qwen 加载一次、结束缓冲归零。报告：`data/verification/model-segmentation-20261023T042606Z.json`。
- 服务已按新装配方式重启，健康检查正常。验证未播放音频或打开设备。

以上是接口、依赖和组合功能的验证。分段准确率、小时级稳定性和既有 Qwen SDPA 长段窗口问题的待办保持不变。

原生运行时迁移：识别适配器只调用固定 C ABI，GGUF 下载/配置在服务层，DLL 发布在外层脚本。`Recognizer.close()` 在服务切换模型/正常退出时释放权重；旧实现只在开发者本地归档，核心不导入归档。
