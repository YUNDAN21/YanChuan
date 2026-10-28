# Qwen3-ASR 原生 GPU 运行时评测

2026-10-27 开始、10-28 完成。结论：本机可使用 **CrispASR / GGML / Vulkan + Qwen3-ASR 0.6B Q8**，显著缩小 GPU 识别运行时；中日英各一条公开样本与现有引擎输出一致。本轮保留现有生产引擎、配置和发布包，实验独立放在 `experiments/native-qwen/`。

## 体积

| 项目 | 实测大小 |
| --- | ---: |
| 当前便携版完整压缩包 | 3,139,511,104 字节，约 2.92 GiB |
| 当前便携版解压后 | 约 5.06 GiB |
| 其中 PyTorch 目录 | 约 4,167 MiB |
| 最小原生 Vulkan 运行目录，含 VC 运行库及第三方声明 | **81,776,077 字节，约 77.99 MiB** |
| 该原生运行目录 ZIP | **28,632,007 字节，约 27.31 MiB** |
| Qwen3-ASR 0.6B Q8 模型，单独存储 | 1,006,809,760 字节，约 960.17 MiB |

78 MiB 是识别运行时的体积，完整应用还需 WinUI/.NET、Python、音频与 ONNX 端点依赖。最终完整便携版体积须在 Engine 接入和依赖收集调整后重新打包测量。模型继续独立下载，不进入程序包。

最小目录只复制公开上游二进制、许可证与 VC 运行库：

```text
crispasr.exe / crispasr.dll
ggml.dll / ggml-base.dll / ggml-cpu.dll / ggml-vulkan.dll
msvcp140.dll / vcruntime140.dll / vcruntime140_1.dll / vcomp140.dll
LICENSE / THIRD_PARTY_NOTICES.txt / manifest.json
```

没有复制兼容用 `whisper.dll`、量化工具、模型、SDK、CUDA 数学库、PyTorch 或本地配置。`dumpbin /dependents` 核对了直接导入；真实启动则覆盖动态加载的 Vulkan 后端。候选目录验证时 PATH 仅含候选目录、Windows/System32 和 Windows，清除 VULKAN_SDK；由系统显卡驱动提供 Vulkan loader。该测试不覆盖干净机器上的系统组件差异。

## 识别速度与文字一致性

本机 RTX 5060 Laptop 8 GB，驱动 616.92，Vulkan 1.4.351。比较现有 QwenEngine / PyTorch BF16 CUDA 与原生 F16、Q8；同一份 0.6B 源权重转换，固定语言、贪心解码、512 token 上限和 4 个 CPU 线程。每段请求 4 次，后 3 次的中位数用于热推理比较。原生计时包含本机 HTTP 往返与 WAV 解码；PyTorch 从已归一化 PCM 开始。

| 样本 | 音频时长 | PyTorch BF16 CUDA | Vulkan F16 | Vulkan Q8 最小目录 |
| --- | ---: | ---: | ---: | ---: |
| 中文 | 4.20 秒 | 0.3775 秒 | 0.1260 秒 | **0.1110 秒** |
| 英文 | 15.05 秒 | 1.9004 秒 | 0.5653 秒 | **0.4840 秒** |
| 日文 | 7.20 秒 | 1.0832 秒 | 0.2879 秒 | **0.2509 秒** |

三个样本、各方案的四次输出均逐字一致。日语文本保存在 UTF-8 JSON 中；部分早期 PowerShell 控制台因编码显示乱码，不影响报告文件。

这验证了三个短样本的一致性和速度；长会议、混说、噪声、术语及完整 CER/WER 语料评测尚未执行。这里的请求是整段窗口推理，未验证音频 encoder cache 的原生流式接口。

原生首次着色器准备曾使 4.2 秒中文请求耗时约 7 秒。缓存已存在时，Q8 最小目录第一次中文请求为 0.53 秒，热请求约 0.11 秒。原生服务 ready 约 1.7–2.5 秒；PyTorch prepare 约 35.8 秒，但包含的导入/预热步骤不同，二者不作严格冷启动倍数比较。

## 显存

| 方案 | 基线 | 峰值 | 增量 |
| --- | ---: | ---: | ---: |
| PyTorch BF16 CUDA | 2,908 MiB | 5,044 MiB | **2,136 MiB，约 2.09 GiB** |
| Vulkan F16 | 2,977 MiB | 6,382 MiB | **3,405 MiB，约 3.33 GiB** |
| Vulkan Q8 最小目录 | 2,718 MiB | 5,044 MiB | **2,326 MiB，约 2.27 GiB** |

`nvidia-smi` 每约 0.25 秒采样，统计整张显卡，包含其他桌面程序且可能漏掉短峰值。Q8 在单独复测与最小目录复测中都得到 2,326 MiB 增量。最初与另一个 native CLI 同时运行的 Q8 报告不纳入结论。

原生 F16 的显存增量更高；Q8 降至接近现有引擎，但没有测得显存节省。原生日志还显示固定 4,096 上下文的 KV cache 约 448 MiB。后续可研究缓存配置，需另测效果。

## 其他原生实现

`ServeurpersoCom/qwenasr.cpp` 在本机完成 Vulkan shared DLL 与 CLI 编译，并用 BF16 GGUF 成功识别中文，日志确认 `Vulkan0`。其首次中文推理约 8.8 秒，未作常驻热测试；本轮性能表采用已完成三语常驻测试的 CrispASR。

两项目 GGUF 转换布局不同，不可互换。源 BF16 模型缓存未修改；所有转换产物和 SDK 均在被忽略的 `work/native-qwen/`。转换使用独立 uv 环境中的 PyTorch，运行阶段使用原生二进制。Vulkan SDK 以 `copy_only=1` 仅解包到项目 work 路径。

## 代码与证据

- 脚本与命令：[`experiments/native-qwen/README.md`](../experiments/native-qwen/README.md)。
- 汇总：`data/verification/native-qwen-20261027/comparison.json`。
- 对照：`torch-bf16/report.json`；F16：`vulkan-f16/report.json`。
- Q8 独立复测：`vulkan-q8-isolated/report.json`；最小目录：`vulkan-q8-minimal/report.json` 及 `server.stderr.log`。
- 候选运行包：`work/native-qwen/runtime-vulkan-minimal.zip`；目录内含每文件 SHA256 清单。
- 已完成脚本 lint、格式及 uv 锁文件检查。本轮全程无采集、无播放，原有录音、历史报告和模型保留。

生产迁移下一单元：提供可注入的 native Engine 适配，验证 PCM/增量分段、模型复用、停止收尾和长文件；然后调整下载清单与发布依赖，去除 PyTorch/Qwen Python 运行链并重新打包。采集、Silero/Smart Turn、会议、翻译、总结及 WinUI 保持原有边界。

## 固定来源

- [CrispASR v0.8.45](https://github.com/CrispStrobe/CrispASR/releases/tag/v0.8.45)，二进制版本 e58bb48b，2026-10-26；下载 ZIP 的 SHA256 为 `714b5de48aca97c2e0b36fbd0c20b5ce2be2ec95bb0880df744aa6420eb93c5b`；[该版本服务接口](https://github.com/CrispStrobe/CrispASR/blob/v0.8.45/docs/server.md)。
- [qwenasr.cpp 固定提交 2843298](https://github.com/ServeurpersoCom/qwenasr.cpp/tree/284329858952aa430326b5166f0062aee3da2989)，GGML 子模块 cc5df49a6b878db2b68b9a7b45976e279594627d。
- [Qwen3-ASR 官方模型](https://huggingface.co/Qwen/Qwen3-ASR-0.6B)；中文、英文使用项目已有公开测试音频。
- [日语测试音频来源](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tree/main/test_wavs)。
- [LunarG Windows SDK 文档](https://vulkan.lunarg.com/doc/view/latest/windows/getting_started.html)，本机文件解包版本 1.4.363.0。
