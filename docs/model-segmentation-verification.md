# 模型控制实时识别段：实施与验证

日期：2026-10-23。状态：核心 API 与组合测试应用已接入，功能验证通过。

## 当前行为

1. Silero VAD 检测语音，静音约 0.6 秒时提供候选边界。
2. Smart Turn 分析当前段最近最多 8 秒音频。达到结束阈值则确认当前识别段，随后语音使用新段 ID；未达到则继续当前段。
3. 每次停顿只判断一次，后续恢复说话再产生下一个候选；临时字幕仍约每秒修订。
4. 不再在 8 秒处正常强切。默认最多 16 秒，或最后一次语音后等待 2 秒仍未结束时兜底确认。停止输入时精确收齐尾部，无需再次运行端点模型。

`Segment.boundary` 区分 `semantic_endpoint`、`max_duration`、`silence_timeout`、`end`。模型确认也直接触发句子组收束，供外层翻译、保存音频等组合使用。

## 模块与调用

- `endpoints/protocols.py` 提供 `SpeechDetector`、`EndpointDetector`；`model_segmentation.py` 只负责 `EndpointOptions` 和 `ModelSegmenter`，不导入 HTTP、设备、归档或模型运行时。
- `endpoints/silero.py` 与 `endpoints/smart_turn.py` 分别提供独立 `from_path` / `prepare` / `probability` API，固定 CPU provider，不依赖彼此、识别引擎或分段器。组合保留在测试应用；模型会话可复用，VAD 状态每个流独立。Smart Turn 特征为独立纯 NumPy 实现。
- 核心 `Recognizer.create_stream(segmenter=...)` 接收策略。基础库不自动加载检测模型，测试应用负责默认装配模型策略、预热和模型生命周期。
- 可选依赖 `endpoint`，根项目和测试应用各自使用独立 `.venv`。下载脚本固定 revision 并校验 SHA-256；已有研究缓存和历史数据保留。

详细独立调用示例见 `docs/python-api.md` 的模型分段章节。上传文件仍默认用约 20 秒解码块，不随本次实时策略改变。

## 自动验证

- 解耦复核后核心 70 项通过；测试应用 43 项通过。
- 覆盖拒绝停顿后继续同一段、接受候选后换 ID、超过 8 秒不换段、持续讲话长度兜底、拒绝后静音超时、纯静音不推理、任意包长、精确尾段、取消和模型异常释放。
- 修正 VAD 的 32 ms 帧跨 100 ms 输入包时需要额外前导保留的问题，避免请求已经释放的句首音频。
- 短/8 秒/超长输入的 NumPy Smart Turn 特征与官方 Whisper 特征流程在 `1e-5` 容差内一致；共享会话下 VAD 状态独立。
- 应用侧确认模型预热只执行一次、后续创建独立流，缺失模型明确报错且不会打开采集设备，模型策略确实接入字幕会话并接受/拒绝候选切点。
- 阻断 ONNX、Transformers、Torch、HTTP 和测试应用模块后，基础核心仍可导入、运行。
- Ruff 与两个锁文件检查通过；解耦后 wheel 成功构建，检查 27 个条目，不含 HTTP、网页、设备或测试应用。新增独立模块的说明见 `docs/decoupling.md`。

## 真实模型验证（无声）

命令：`uv run --no-sync python scripts/verify_model_segmentation.py`。

使用本机 Silero + Smart Turn v3.2 CPU，Qwen3-ASR-0.6B CUDA/BF16；公开中英文样本直接解码为 PCM，追加一秒静音用于观察停顿，不采集或播放音频。真实报告：`data/verification/model-segmentation-20261023T040845Z.json`。

| 项目 | 中文 | 英文 |
| --- | ---: | ---: |
| 确认段 | 1 | 1 |
| 确认边界 | semantic_endpoint | semantic_endpoint |
| 该段时长 | 3.632 秒 | 14.608 秒 |
| 临时更新次数 | 3 | 14 |
| Smart Turn 调用 | 1 | 1 |
| ASR 活动缓冲峰值 | 3.956 秒 | 14.952 秒 |
| 检测上下文/尾部峰值 | 5.200 秒 | 8.028 秒 |
| 停止后两处音频缓冲 | 0 | 0 |

两个检测模型均报告 `CPUExecutionProvider`，Qwen 两个用例共加载一次。英文实际识别段超过 8 秒，仍使用同一个段 ID，最终由端点模型确认；不只是把固定块文本在最后合并。

本地测试服务已重启于 `http://127.0.0.1:8765/`。应用装配验证确认默认 `model` 策略和 16 秒兜底，两个分段器共享 CPU 模型、各自独立 VAD 状态；健康检查正常。没有为验证而打开真实录音设备。

## 限制

这证明功能链路与资源边界，不是断句准确率、日语/混说或噪声质量评测，也不是小时级稳定性验收。实时识别仍重算当前未确认段，尚无原生增量编码缓存。此前记录的上游 Qwen Transformers SDPA 长输入窗口掩码问题仍存在，本次没有修改第三方推理实现；不能用本报告宣称长输入已达到其训练时窗口计算的一致性。
