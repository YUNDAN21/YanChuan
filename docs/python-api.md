# Python API：识别核心与外层组合

识别库不依赖 FastAPI、网页、WASAPI、归档模块或 `.env`。公开入口是 `livecaptions_asr`，只导入它不会加载模型、打开设备、创建文件或启动后台线程。文件解码使用 NumPy、SciPy 和 SoundFile；Qwen 是可替换的引擎适配器。

## 单行短字幕拆分

`SubtitleSplitter` 独立接受识别文字，不加载模型、改变识别音频边界或改写原文。利用已有句末/分句标点、连接词、词边界优先切分，无合适边界时按长度或宽度继续拆。它是文字规则，不是额外的语义模型。

```python
from livecaptions_asr import SubtitleSplitter

splitter = SubtitleSplitter(max_units=48)  # 中日字符为 2 单位，ASCII 为 1
cues = splitter.split("先确认实时字幕的显示效果，然后讨论下一步的会议记录功能。")
for cue in cues:
    print(cue.start, cue.end, cue.text, cue.boundary)

# 界面可额外提供实际字体的宽度约束；核心不依赖 Qt。
cues = splitter.split(text, max_width=available_width, measure=font_measure)
```

每个 `SubtitleCue` 包含原文字串的 Python 字符索引 `start/end`、原样 `text` 和切分依据 `boundary`（sentence/clause/phrase/word/width/end）。非空输入的 cue 文本可按顺序拼回原文，包含空白；全空白输入返回空元组。`measure` 与 `max_width` 成对提供，宽度须为有限正数，单字符也无法容纳时返回 `subtitle_width_too_small` 错误。

cue 不生成语音时间戳；原始时间范围仍取自 `Sentence/Segment`。调用方保留完整原始结果，并可在文字修订或窗口宽度变化时重新拆分。

## 可选启动计时

`StartupEvent(stage, state, seconds)` 是无 I/O 的不可变事件；`StartupObserver` 接收该事件。`state` 为 `started` / `completed` / `failed` / `reused` / `cancelled`，`seconds` 为该阶段墙钟秒数，开始与复用为零。`StartupTimer(observer).phase(stage)` 可用于外层组合的同类观测；父阶段包含子阶段，耗时不可重复相加。

```python
from livecaptions_asr import Recognizer
from pathlib import Path
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine

events = []
engine = NativeQwenEngine(NativeQwenOptions(
    model_path=Path("models/native/qwen3-asr-0.6b-q8_0.gguf"),
    library_path=Path("runtime/native/win-x64/crispasr.dll"),
    device="vulkan",
), on_progress=events.append)
recognizer = Recognizer(engine)
recognizer.prepare(on_progress=events.append)
```

适配器报告 `native_model_validate`、`native_library`、`native_weights`；`Recognizer.prepare` 报告 `recognizer_prepare`、`recognizer_queue`、`engine_load` 和 `warmup`。权重常驻，多次准备报告 `native_model/reused`。状态包含原生版本、精度、实际 GPU 名称、加载次数和耗时。失败阶段报告 `failed`。

计时不改变 `RecognitionEngine.load()` 协议、`EngineStatus.load_seconds` 或默认无回调调用方式；观察者异常写入标准日志，识别异常保持原样。核心不保存计时文件；文件日志、界面进度和应用总启动时间由外层管理。

## 安装边界

核心库使用 uv 和根目录 `.venv`；组合测试应用有自己的 `apps/asr-test/.venv`：

```powershell
# 基础库与自定义引擎，不安装项目的 HTTP / WASAPI / Qwen 依赖
uv sync --no-dev

# 使用真实 Qwen 引擎
uv sync --no-dev

# 核心开发环境和 Qwen
uv sync

# 独立组合测试应用
uv sync --project apps/asr-test --all-extras
```

核心与原生引擎不安装 Torch、Transformers 或 qwen_asr。GGUF 下载、存储目录、默认显卡和 DLL 定位由应用层提供；核心只接受显式参数。`endpoint` extra 安装 ONNX Runtime。
## 建立模型实例

```python
from livecaptions_asr import Recognizer, RecognizerOptions
from pathlib import Path
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine

recognizer = Recognizer(
    NativeQwenEngine(NativeQwenOptions(
    model_path=Path("models/native/qwen3-asr-0.6b-q8_0.gguf"),
    library_path=Path("runtime/native/win-x64/crispasr.dll"),
    device="vulkan",
)),
    options=RecognizerOptions(chunk_seconds=20, semantic_seconds=60),
)
recognizer.prepare()  # 可选：加载和预热；不启动采集
print(recognizer.status())  # EngineStatus
```

`NativeQwenOptions` 接受模型/DLL 路径、model_id、device、precision、threads 和 max_new_tokens；不读取环境变量或自动下载。`device="vulkan"` 优先独显，也可指定 `VulkanN` 或 `cpu`。GPU 失败不回退 CPU。
一个实例串行识别并复用权重；结束使用后调用 `recognizer.close()` 或 `engine.close()`。可用 `with NativeQwenEngine(options) as engine` 管理加载/释放。多次 close 安全，再次 prepare 可重载。
`RecognitionEngine` 仍是 load/status/recognize 协议，释放与原生预热分别通过可选 `ReleasableEngine` / `WarmableEngine` 协议组合；通用分段不依赖原生适配器。

## 单个窗口识别

```python
from livecaptions_asr import AudioData

result = recognizer.recognize(
    AudioData.from_pcm16k(pcm), language="zh", context="可选专业词提示",
)
print(result.text, result.timing.inference_seconds)
```

`pcm` 是 16 kHz、单声道、浮点采样数组，常规幅度在 `[-1, 1]`。立体声混音、其他采样率、PCM 字节到浮点的转换由输入适配器负责。语言为 `auto` / `None`、`zh`、`ja`、`en`。

## 长文件逐段识别

```python
def on_segment(progress):
    print(progress.processed_seconds, progress.segment.text)

result = recognizer.transcribe_file(
    "meeting.wav", language="auto", on_segment=on_segment,
)
print(result.text)
print(result.segments, result.semantic_sections)
```

接受字符串路径、`Path` 或可 seek 的二进制文件对象。默认解码器逐段读取 WAV / FLAC，末端优先找静音，默认窗口约 20 秒，不一次加载整个录音。回调参数为 `FileProgress`，包含完成时长、段数、当前 `Segment` 与本次形成的句子组；最终 `TranscriptionResult` 还包括收尾形成的句子组。

传入 `cancel=threading.Event()` 可在块间及模型锁等待结束后取消；当前模型调用不会被强制中断，已发出的进度回调由调用方保留。外部提供的文件对象由调用方关闭。音频内存按窗口限制，返回的完整转录文字和分段元数据仍随文件长度增长。

## 任意来源的 PCM 增量识别

```python
stream = recognizer.create_stream(language="zh")

def handle(event):
    if event.kind in {"partial", "final"}:
        segment = event.segment
        # 同一 id 的 partial 会修订；final 确认这一段
        print(event.kind, segment.id, segment.revision, segment.text)
    elif event.kind == "sentence":
        print("完整句子组", event.sentence.text)

for pcm in audio_source:  # 麦克风、文件、网络或其他适配器提供
    for event in stream.feed(pcm):
        handle(event)

for event in stream.finish():  # 收齐不足 100 ms 的音频尾部和待确认文字
    handle(event)
```

- `feed` / `finish` 返回同步事件迭代器，每次必须完整消费后再调用下一次。同一会话由一个调用方顺序驱动，线程和异步队列由外层管理。
- 输入是连续时间轴的 16 kHz 单声道浮点 PCM，时间从零开始；不在核心中根据墙上时钟补静音。建议每次送入 100 ms—1 秒，任意包长都会按统一 RMS 帧重新组合。
- 默认约每 1 秒修订，静音约 0.6 秒确认，连续声音约 8 秒分块。当前是分块重复推理，不是 Qwen 原生流式。
- 核心只保留活动识别窗口、短暂前导音频及待处理小尾部，不保存所有 PCM 或事件。默认 PCM 保留量约 8 秒级；调用方消费结果后决定是否保存。
- `finish()` 可重复调用，后续不再产生事件；结束后 `feed()` 抛出 `ASRError(code="stream_closed")`。
- `cancel()` 丢弃未完成部分并释放音频，不再执行模型推理。关闭尚未消费完的事件迭代器也会取消该会话。
- 多个流共享一个 `Recognizer`，分段 ID、修订号、音频缓冲、语言和句子组合状态各自独立。ID 在会话内部唯一，跨会话存储时由外层附加会话 ID。
- `feed(pcm, emit_partial=False)` 可在追赶积压时省略临时推理，确认段与句子事件仍正常发出。

`StreamOptions` 独立控制门限、静音时长、最大段长、更新间隔与句子组长度；不包含设备、路径、HTTP 或客户端心跳参数。

## 模型决定识别段边界

安装可选依赖并显式下载模型（构造器不会联网）：

```powershell
uv sync --extra endpoint
uv run --no-sync python scripts/download_endpoint_models.py
uv run --no-sync python scripts/verify_model_segmentation.py
```

```python
from pathlib import Path
from livecaptions_asr import EndpointOptions, ModelSegmenter, SileroVADModel, SmartTurnEndpoint

vad_model = SileroVADModel.from_path(Path("models/endpoint/silero_vad.onnx"), threads=1)
endpoint = SmartTurnEndpoint.from_path(Path("models/endpoint/smart-turn-v3.2-cpu.onnx"), threads=1)
vad_model.prepare()  # 可选；在外层打开采集设备前预热
endpoint.prepare()
segmenter = ModelSegmenter(vad_model.create_detector(), endpoint, EndpointOptions(
    silence_seconds=0.6,
    max_wait_seconds=2,
    max_segment_seconds=16,
    endpoint_threshold=0.5,
))
stream = recognizer.create_stream("zh", segmenter=segmenter)
# 与原 API 相同：逐批消费 stream.feed(pcm)，最后 finish() 或 cancel()。
```

- `ModelSegmenter(speech, endpoint, options)` 可直接注入任意符合 `SpeechDetector` / `EndpointDetector` 协议的检测器，不依赖 ONNX、Qwen、设备、线程或 HTTP。概率须为 `[0,1]`；分段器每个流独占。
- VAD 按 512 样本（32 ms）检测。0.6 秒静音后只对该次停顿判断一次；拒绝后，同一识别段继续接收语音，下一次停顿再判断。不会每个静音帧重复调用端点模型。
- 接受候选产生 `boundary="semantic_endpoint"`，默认保留 200 ms 前后沿。模型只看当前段最近最多 8 秒，长段不会因此在 8 秒处强切。
- 最长静音等待从最后一次检测到语音开始计时。拒绝后仍静音达到 2 秒，产生 `silence_timeout`；连续段达到 16 秒产生 `max_duration`。受 VAD 帧粒度影响，边界可能超过段长上限一个约 32 ms 的帧。
- 临时识别继续约每秒修订当前段，仍是当前段重复推理，不是增量编码器缓存。尚未确认的整段 PCM 上限由 `EndpointOptions` 控制，不由 Smart Turn 的 8 秒上下文控制。
- `finish()` 不需要模型批准，精确收尾；`cancel()` / 异常 / 事件迭代器提前关闭会清空 ASR 与检测音频缓冲并重置 VAD 状态。分段器提供幂等 `close()`。
- `SileroVADModel` 与 `SmartTurnEndpoint` 各自单独加载、预热和调用，不创建或依赖分段器，也不依赖彼此。`.providers` 返回各自的实际 provider；`threads` 约束 ONNX，不修改调用方的全局 BLAS / PyTorch 配置。组合及共享生命周期由调用方负责，核心没有两模型绑定加载的入口。
- 同一 `SileroVADModel` 的 `.create_detector()` 可共享模型会话但创建独立循环状态。得到的 detector 直接 `.probability(frame)` / `.reset()`；`SmartTurnEndpoint` 直接 `.probability(pcm)`，无需 VAD 或 ASR。
- `EndpointDetector.context_samples` 声明所需音频上下文预算。`ModelSegmenter` 据此保留上下文，不写死 Smart Turn 的 8 秒；可注入其他长度的端点实现。Smart Turn 本身声明 128000 样本，即 8 秒。
- Smart Turn 预处理使用独立纯 NumPy `WhisperEndpointFeatures`，覆盖前补零、归一化、STFT、Slaney mel 和截取最近 8 秒；测试与官方公开 Whisper 特征流程在短/完整/长输入上对齐。检测运行代码及 `endpoint` extra 不依赖 Transformers 或 Torch。
- 根包公开导出按功能延迟导入。单独调用检测模型不会导入识别引擎、PCM 流、分段器或测试应用；只导入基础库也不会导入 ONNX 运行时。
- `semantic_endpoint` 确认段可直接收束 `Sentence`，无需等待标点启发式再次批准。强制长度切段仍可由组合器与后续识别段组合。

自定义分段器若需要超过默认 200 ms 的前导保留，可提供有界 `lookback_seconds`；PCM 会话按需保留，防止 VAD 帧跨输入包时丢失句首。使用当前 Smart Turn 配置时，检测器最多保留约 8 秒上下文，ASR 活动缓冲最多约 16 秒；两者不是完整录音归档。

以上能力针对 PCM 增量接口。长文件 `transcribe_file` 仍使用独立的约 20 秒解码块；如需按模型端点处理文件，可把解码块逐批送入同一 `PCMStream`。全量文件模式改为模型分段不属于本次改动。

## 可替换边界与外层职责

| 接口 | 职责 |
| --- | --- |
| `RecognitionEngine` | 模型加载、状态与一次窗口识别 |
| `FileDecoder` | 从输入源产生 `AudioChunk`，可由 `Recognizer(decoder=...)` 注入 |
| `WindowSegmenter` | 音频窗口的临时/确认边界，可在 `create_stream(segmenter=...)` 注入 |
| `SentenceGrouper` | 将确认的 `Segment` 组合为 `Sentence`，可在 `create_stream(grouper=...)` 或 `transcribe_file(grouper=...)` 注入 |
| `Recognizer` | 共用推理入口、模型复用、容量和文件流水线 |
| `PCMStream` | 纯 PCM 增量处理，输出 `StreamEvent` |

自定义流分段器遵循 `WindowSegmenter` 协议，`start` 表示仍需保留的活动窗口起点；不得请求已释放或尚未输入的音频，并应自行限制最大窗口。每个流使用独立的分段器和组合器实例。

采集模块只提供 PCM；归档模块另行保存全部输入，并订阅 `sentence` 事件导出句子 WAV。HTTP / 桌面 / 命令行按需要消费同一事件，核心无需知道使用了哪种入口。设备、归档、HTTP、配置和 CLI 都已移到独立的 `apps/asr-test/src/livecaptions_testapp/`，该组合应用只是测试工具。

公开结果都是不可变 dataclass，使用属性访问。仅在 HTTP、JSON、存储边界调用 `.to_dict()`；库级错误统一为带 `code` / `message` 的 `ASRError`，外层决定如何展示或重试。

## 无声运行示例与验证

```powershell
uv run --no-sync python examples/compose.py data/samples/asr_zh.wav --mode stream
uv run --no-sync python scripts/verify_library.py
```

以上命令只读文件、执行推理和打印文字，不播放音频、不打开采集设备、不启动 HTTP。真实验证报告按时间戳保存在 `data/verification/core-api-*.json`。设备播放测试已移到 `apps/asr-test/scripts/`，默认拒绝运行；夜间不执行。
