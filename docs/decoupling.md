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

笔记的分组、正文和摘录去重由会议包 `NoteStore` 维护，桌面服务 `NotesService` 负责按显式会议/请求 ID 读取已完成回答。JSONL 只路由，WinUI `NotesViewModel` 负责调用和选中项、`NotesDialog` 负责编辑呈现；核心识别不依赖笔记。笔记数据库独立于会议数据库，删除会议不删除笔记。

录音中切换监听来源由采集包 `SwitchableCapture.set_source()` 提供可选异步能力，`SessionRunner` 转发给正在运行的采集器。WASAPI 所在线程负责设备事务、同一时间轴及有界音轨；桌面服务负责会议状态校验、请求归属和结果事件。JSONL 仅路由 `session.source`，WinUI 只发请求并投影确认状态；核心识别、语义分段及会议保存无需重建。

非阻塞停止由采集包 `StoppableCapture.request_stop()` 提供，`SessionRunner` 在识别消费之外观察停止/取消信号；WASAPI 固定截止点并在设备线程关闭输入。会议包将 SQLite 总结提交与可重试文件导出分开，`MeetingStore.retry_export()` 不触发模型；服务和前端只转发、显示导出状态。AI 请求头和安全 HTTP 错误分类集中在会议包传输层，模型列表、总结、问答及外层翻译适配器共享实现。

聊天压缩的 SQLite 状态、分批压缩和消息筛选位于会议包 `chat_context.py`，通过注入的 ChatClient 调用模型；`ChatCoordinator` 统一问答/压缩互斥、取消和会议归属。`MeetingReference` 提供不可变的原文快照及只读目录/搜索/分页 API，`MeetingAgent` 负责模型驱动的查阅循环、整场覆盖、请求预算和引用回查。桌面服务装配客户端与快照，JSONL 仅路由，WinUI 只显示上下文和活动状态。手动时间范围逻辑已移除，原文和摘要导出不改变。

托盘的图标、菜单和窗口恢复由 WinUI `DesktopTray` 管理，关闭/隐藏与正常收尾由独立 `WindowLifetime` 协调；隐藏窗口不会调用后台停止接口。桌面服务仅持久化 `close_to_tray` 偏好，核心识别和采集库不依赖托盘或窗口状态。

问答与压缩共用 `ChatCoordinator` 的任务生命周期，每个任务持有请求归属、取消信号和数据库连接。活动任务与仍未返回的后台任务分开登记，取消后可立即重试；旧任务只能释放自己的连接，不能更新新任务。启动线程失败也会保存失败状态并解除互斥。

会议问答优先完整提交：`MeetingAgent` 根据会议原文、聊天历史、提示词和回答预留共同判断上下文预算；能够容纳时一次请求，不设置独立的短会议长度门槛。超过预算才使用搜索/分页读取/整场遍历，会议包统一返回同一策略的用量估计，WinUI 只显示。`ChatCoordinator` 提供可注入任务时限（默认 180 秒）与等待进度，超时解除互斥，迟到结果不更新记录；传输层记录协议、耗时和数值统计，不记录密钥、正文或完整返回体。

完整提交的原文由 `MeetingAgent` 放在首条用户消息前部，聊天、压缩摘要、问题及会随录音变化的概览放在原文之后；不重复原文，不改变对话角色。新增转写保留已有片段前缀，客户端为渠道提供可复用输入。传输层统一读取 Responses、Chat Completions 与兼容渠道的缓存 token 字段，缺失记为未知，明确的零记为零。实际缓存行为及计费以渠道返回为准，不添加本地伪缓存或开启远程对话存储。

模型目录与类型分派在 `models.py`，GGUF 查找/下载在 `native_assets.py`，端点模型与 ONNX 检查在 `endpoint_assets.py`；两个适配器都不反向导入管理层。AI HTTP opener、请求头及错误分类由 `http_transport.py` 统一提供，模型列表不依赖总结客户端的私有实现。WinUI 退出状态只由 `WindowLifetime` 管理，主窗口读取该状态，不另存关闭状态。
