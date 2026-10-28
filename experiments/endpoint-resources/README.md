# 分段模型 CPU 资源实验

独立研究项目，不被核心 API 或测试应用导入。依赖及 `.venv` 完全独立，无 Torch、CUDA、音频播放或设备采集。只测试 Silero VAD ONNX 和 Smart Turn v3.2 int8 CPU 的资源开销，尚未接入主识别链路。

在仓库根目录执行：

```powershell
uv sync --project experiments/endpoint-resources --locked
uv run --project experiments/endpoint-resources --no-sync python experiments/endpoint-resources/download.py
uv run --project experiments/endpoint-resources --no-sync python experiments/endpoint-resources/benchmark.py
```

需要已有公开样本 `data/samples/asr_zh.wav`。下载脚本固定上游 revision、验证 SHA-256，模型放在忽略目录 `models/endpoint-benchmark/`。实验输出位于 `data/verification/endpoint-resources/`，重跑会更新 `silero.json` / `smart.json` / `combined.json`；第一次测量另保留 `*-first.json`。

每个模式使用新进程；ONNX Runtime 强制 CPU，intra/inter 推理线程均设 1、关闭空闲自旋。Silero 采用上游的 512 样本加 64 样本上下文、循环状态约定。Smart Turn 按上游方式归一化并提取最近 8 秒 Whisper 特征，使用 NumPy 特征提取路径；不加载完整 Whisper ASR。

测量阶段包括基础 Python、运行库导入、特征提取器导入、模型加载、预热和循环推理。内存记录进程 RSS、Windows private bytes 和 peak working set，不能当作模型张量大小，也不能直接当作主应用集成后的新增量。

组合调度实验是 120 秒音频时间线、VAD 每 32 ms 一次、端点每 2 秒一次，不等待真实时间。它只测给定调用频率的资源成本，不根据真实 VAD 事件触发，也不验证准确率或小时级稳定性。

完整结果与限制见根目录 `docs/endpoint-resource-benchmark.md`。
