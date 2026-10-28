# Silero VAD 与 Smart Turn 的本机资源基线

日期：2026-10-23。用途：先判断本地分段模型的资源开销，再决定是否集成。本轮未更改核心 API、当前分段参数或 Qwen 识别后端。

## 测量条件

- 本机 Intel Core Ultra 7 356H，16 个逻辑处理器，约 32 GB 内存；Windows。
- 独立 `experiments/endpoint-resources/.venv`，uv 管理；Python 3.12.14、ONNX Runtime 1.31.0、NumPy 2.5.4、Transformers 4.57.6。未安装/导入 Torch。
- 强制 `CPUExecutionProvider`，ONNX intra/inter 推理线程均为 1，关闭空闲自旋。进程本身仍有运行库等辅助线程。
- Silero：上游固定提交 `066409f3d9db45a30d21dfcbf9147826ffdd7052` 的标准 `silero_vad.onnx`。
- Smart Turn：固定模型 revision `f766f81d3cfdf7737ac64aad813d91bbfd56bf93` 的 `smart-turn-v3.2-cpu.onnx`，int8 量化。
- 使用公开中文 WAV 的重复 PCM；每个模式独立新进程，先预热，连续完成两轮资源测量。全程没有播放声音或打开音频设备。

## 模型文件与进程内存

| 组合 | 模型文件合计 | 测试结束 RSS | 进程峰值 working set | 模型及工作区等新增 RSS* |
| --- | ---: | ---: | ---: | ---: |
| Silero 单独 | 2.22 MiB | 77.2–79.7 MiB | 78.4–89.8 MiB | 18.6–21.2 MiB |
| Smart Turn 单独 | 8.28 MiB | 128.8–131.2 MiB | 135.9–137.8 MiB | 47.7–47.9 MiB |
| 两者同进程 | 10.50 MiB | 141.0–141.1 MiB | 146.7–146.8 MiB | 60.5–60.9 MiB |

\* 新增 RSS 以已经导入该模式运行库与特征提取器后的阶段为基线，包含模型会话、推理工作区、样本缓冲和测量循环的小量开销。不是对模型张量的逐一计数。完整进程 RSS 包含 Python、NumPy、ONNX Runtime 等。两模型共享运行库，不能将两份独立进程占用直接相加。

组合进程的 private bytes 约 152.4–152.7 MiB。这是 Windows 私有提交量，与实际驻留 RSS 不同，可能大于 RSS。

本实验使用纯 CPU 推理，没有加载 GPU provider 或创建 CUDA 模型分配，因此模型不需要额外 GPU 显存；这不表示整台机器的 GPU 使用量为零。未用整卡显存读数计算模型占用。

独立实验 `.venv` 当前逻辑文件大小约 159.8 MiB，另有约 10.5 MiB 模型文件；不包含共享基础 Python、uv 下载缓存或主项目。此数字是当前依赖环境的磁盘体积，不是运行内存。

## CPU 耗时与调用频率

| 操作 | 每次输入 | 预热后中位耗时范围 | P95 范围 |
| --- | --- | ---: | ---: |
| Silero VAD，含循环状态/上下文封装 | 512 样本，即 32 ms 音频 | 0.087–0.091 ms | 0.104–0.172 ms |
| Smart Turn 完整处理，含 NumPy 特征提取 | 最近 8 秒 PCM | 42.0–67.7 ms | 44.8–80.9 ms |
| Smart Turn 仅 ONNX，使用预先算好的特征 | 同一个 8 秒特征 | 34.6–61.4 ms | 38.6–66.1 ms |

Silero 每个模式测 4,000 次；Smart Turn 每个模式的完整管线和仅 ONNX 各测 60 次，各自先预热 10 次。跨进程范围受系统调度、混合架构 CPU、后台负载和频率变化影响。不同循环不是同步对照，不能用两个中位数相减来精确推算特征提取耗时。

单路 Silero 每秒约 31.25 次调用，本次处理所耗 CPU 时间折合约 0.28–0.33% 的一个逻辑核心。Smart Turn 是短时突发计算，平均成本取决于实际调用次数；应在候选停顿时运行，不能每个 VAD 帧都调用。

组合调度实验用 120 秒音频时间线，执行 3,750 次 VAD 和 60 次 Smart Turn，即每两秒做一次端点判断：两轮分别消耗 4.969 / 5.000 CPU 秒，折合一个核心约 4.14 / 4.17% 的平均计算预算。计算过程未等待真实时间；这是按固定频率折算的资源量，不是任务管理器中整机 CPU 的实时百分比，也不是实际会议中端点调用次数的预测。

两轮组合实验在预热后、循环测量后、120 秒时间线模拟后的 RSS 基本持平。该短实验不能替代小时级稳定性测试。

## 对当前项目的含义

- 资源上可以把 CPU 版两模型作为候选：本机组合测试进程约 141 MiB RSS、峰值约 147 MiB，不挤占 Qwen 的 GPU 模型预算。
- Smart Turn 使用固定的最近 8 秒输入；16 kHz float32 单声道的这部分 PCM 约 0.49 MiB。可以用有界环形缓冲，避免随整场会议增长；模型内部仍需要额外工作区。
- 当前实验是独立进程的资源基线，尚未测同进程集成 Qwen 后的实际增量、同时负载下对字幕延迟的影响或噪声环境中的调用频率。
- 未评价中文、日语、英语的断句准确率。Smart Turn 预测发言是否结束，不等同于话题级分段。资源可接受不代表已经确认分段质量。

## 复现与原始结果

按 `experiments/endpoint-resources/README.md` 在独立环境运行下载和基准。原始数据位于 `data/verification/endpoint-resources/`，含两轮六份 JSON，记录文件 SHA-256、实际 provider、分阶段内存、推理时间与 CPU 时间。

## 官方依据

- [Silero ONNX 状态和调用约定](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py)
- [Silero 项目说明](https://github.com/snakers4/silero-vad)
- [Smart Turn CPU 量化与 8 秒输入说明](https://github.com/pipecat-ai/smart-turn)
- [Smart Turn 模型文件](https://huggingface.co/pipecat-ai/smart-turn-v3/tree/f766f81d3cfdf7737ac64aad813d91bbfd56bf93)
- [Smart Turn 推理流程](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py)；其示例默认文件名仍是 v3.1，本实验显式选择 v3.2 CPU 文件，不依赖该默认值。
