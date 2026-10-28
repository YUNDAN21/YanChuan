# Qwen3-ASR 流式计算与显存核查

核查日期：2026-10-23。范围：本项目安装的 `qwen-asr==0.0.6`，及其 vLLM extra 所固定的 `vllm==0.14.0` 源码。这里讨论官方 Python 流式封装，不泛指所有 Qwen3-ASR 实现，也不把它等同于 vLLM 后续版本的 realtime 接口。

结论：这版封装会反复提交当前 stream state 从开始到现在的全部音频。普通内容哈希路径下，追加音频使整个音频项目的缓存键变化，因此不能依靠 vLLM 自动复用历史音频编码或音频部分的 KV。内存和显存有不同的增长机制；不能用桌面总显存读数推算模型独占显存。当前项目的有界分段实现没有因此改为官方无限累积封装。

## 源码与行为证据

1. `init_streaming_state` 创建空 `audio_accum`；`streaming_transcribe` 每次追加 PCM，构造单个完整音频项目，然后重新调用 `generate`。`finish_streaming_transcribe` 对尾部使用相同路径。调用方可以在句段结束时创建新状态，但该封装不会自行截断历史。
2. 原始方法体的输入记录探针：连续送入 2、2、2、0.25 秒，底层 `generate` 实际收到 2、4、6、6.25 秒。此探针替换了推理器，不是实际 vLLM 推理。
3. vLLM 的默认多模态哈希包含整个 NumPy 音频数组的类型、形状和内容。Qwen 包装器没有传递自定义 UUID。音频追加后，旧音频项目的缓存键不再匹配。
4. vLLM 编码缓存以完整多模态项目的标识查找。探针重复相同数组命中，追加数组不命中；容量不足时可淘汰不再被请求引用的旧项。不是按 PCM 前缀自动复用部分编码。
5. Qwen vLLM 适配器的 `_process_audio_input` 调用音频塔处理该输入的所有特征；虽然签名中有 `audio_hashes` 和 `cached_audio_features`，方法体没有使用它们。内部约 8 秒的局部注意力窗口不等于跨 `generate` 请求的窗口缓存。
6. vLLM 的 KV 块哈希包含多模态标识和前一块哈希。即使假设占位 token 完全相同，音频标识改变也会使音频所在块以及后续块失配；音频前完整的相同文本块仍可能复用。探针设置块长 16、音频起点 32，六块的相等关系为 `[true, true, false, false, false, false]`。

以上是从具体版本代码推导、并用抽取的原始方法体核对的结果，不是运行中的 vLLM 命中率测量。序列化比较探针用 SHA-256 替代 BLAKE3，只验证缓存键的相等关系；未据此宣称实际哈希值。缓存管理、块额外键和父块组合使用原始方法体。

官方仓库的 [Discussion #9](https://github.com/QwenLM/Qwen3-ASR/discussions/9) 中，2026-01-30 的回复也说明这份实现主要演示算法计算过程，没有实现 KV 复用。核查时仍以本机安装版本及固定依赖源码为准。

## 哪些资源会增长

| 部分 | 行为 |
| --- | --- |
| 模型参数 | 模型加载后基本固定；本机 BF16 参数合计 1,564,852,224 字节，约 1.457 GiB |
| 原始 PCM | `audio_accum` 是 CPU NumPy 数组，16 kHz / 单声道 / float32 每秒 64,000 字节，一小时约 219.7 MiB RAM；拼接时还有临时副本 |
| 音频特征、编码与计算工作区 | 当前单次输入越长，计算量和所需空间会增加；重算不是只处理新来的两秒 |
| 文本解码 KV | 音频 token、文本提示及生成 token 都消耗上下文和 KV 空间；没有自动把旧音频转换为常量大小状态 |
| vLLM 显存池 | 启动时规划并分配 KV 池；旧的无引用缓存可淘汰，因此不是把每一轮的 KV 永远相加保存 |

vLLM 0.14.0 的 `gpu_memory_utilization` 默认是 0.9，是该实例的模型执行预算比例，不是仅模型权重的比例，也不是操作系统层面的绝对显存上限。worker 先做内存 profiling，再把预算中扣除非 KV 使用后的部分用于 KV。`kv_cache_memory_bytes` 可指定 KV 预算，并覆盖上述自动预算路径。CUDA 图、临时工作区、其他进程及初始化阶段也会影响可用显存与读数。

所以，显存曲线平稳可能只是池已经预分配，不能证明历史计算得到了复用；池有界也不能保证同一状态的累计输入可以无限增长。请求仍受实际上下文长度、编码预算和 KV 容量限制。

## KV 容量的理论量级

本机 0.6B 配置的文本部分有 28 层、8 个 KV 头、每头 128 维。单并发、标准未压缩 BF16 KV 每 token：`2 × 28 × 8 × 128 × 2 = 114688 bytes`，即 112 KiB。按特征长度公式，整数秒音频约每秒 13 个音频 token。

| 当前状态累计音频 | 音频 token | 仅音频对应的 BF16 KV |
| --- | ---: | ---: |
| 8 秒 | 104 | 11.38 MiB |
| 1 分钟 | 780 | 85.31 MiB |
| 10 分钟 | 7,800 | 853.13 MiB |
| 1 小时 | 46,800 | 5.00 GiB |

这是配置推算，不是显存实测或可运行时长承诺；不含参数、文字 KV、编码工作区、图及分配对齐，且实际部署可能先达到上下文/编码容量上限。多并发或不同 KV 精度需另算。

## 本机真实 CUDA 测量：当前 Transformers 后端

设备 RTX 5060 Laptop GPU，8 GB；Torch 2.11.0+cu128、BF16、SDPA、单并发。使用已缓存权重与公开中文样本的重复 PCM，无设备采集、无播放。先预热，每项前清理无引用分配并重置 CUDA 峰值统计；输出上限使用项目默认的 512 token。

| 单次送入音频 | PyTorch 活跃分配峰值 | PyTorch 保留分配峰值 | 调用结束后的活跃分配 |
| --- | ---: | ---: | ---: |
| 2 秒 | 1.491 GiB | 1.766 GiB | 1.472 GiB |
| 8 秒 | 1.534 GiB | 1.781 GiB | 1.472 GiB |
| 20 秒 | 1.621 GiB | 1.799 GiB | 1.472 GiB |
| 60 秒 | 1.910 GiB | 1.943 GiB | 1.472 GiB |

注意：

- 这些是本进程 PyTorch 分配器的统计，不含全部 CUDA context / 驱动 / 桌面占用；不是 `nvidia-smi` 整卡总占用，也不是 vLLM 测量。
- 首次解码输入分别为 44、122、278、798 token，符合每秒 13 个音频 token 加固定提示的增长。
- 重复语料不是准确率评测。60 秒这一项触及 512 次解码调用上限，不能作为完整长音频转写验收或生产延迟基准。
- 另用 16 token 上限的控制实验得到相同的四项活跃分配峰值，说明本次测得峰值主要出现在输入处理阶段；不据此外推其他音频或无限输出长度。
- 当前捆绑 Transformers 实现还有已报告的 SDPA 窗口掩码问题：[Issue #213](https://github.com/QwenLM/Qwen3-ASR/issues/213)。本机代码确实构造 `cu_seqlens`，却没有在编码器层调用时传入窗口掩码；不能把其长输入测量当作 vLLM 窗口注意力的等价性能。本轮记录问题，未修改依赖或推理行为。

## 复现与项目决定

根项目已安装可选 Qwen 依赖时，在项目根目录执行：

```powershell
uv run --no-sync python scripts/audit_qwen_streaming.py
uv run --no-sync python scripts/probe_qwen_memory.py
uv run --no-sync python scripts/probe_qwen_memory.py --max-new-tokens 16
```

第一条首次下载缺失的 vLLM 固定版本源码到忽略目录 `work/runtime-audit/`，不安装 vLLM；输出报告包含来源 URL 和源码 SHA-256。报告位于 `data/verification/qwen-streaming-source-audit.json`、`qwen-transformers-memory-512tokens.json`。早期 16 token 报告另保存于 `qwen-transformers-memory.json`。

当前实时 API 继续采用有界活动窗口与分段确认。长会议不应维持一个永不结束的官方 stream state。后续原生流式对比必须验证稳定窗口的编码复用、尾部重算和 KV 有效性；不能用给变化音频强塞相同 UUID 的方式伪造命中，这会错误复用不再匹配的特征。当前 Windows 未安装可用 WSL/Linux 运行环境，尚未实际运行官方 vLLM，运行时性能对比继续待办。

## 主要源码

- [Qwen 官方封装](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_asr.py)
- [Qwen vLLM 音频适配器](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/core/vllm_backend/qwen3_asr.py)
- [vLLM 0.14 多模态哈希](https://github.com/vllm-project/vllm/blob/v0.14.0/vllm/multimodal/hasher.py) 与 [处理路径](https://github.com/vllm-project/vllm/blob/v0.14.0/vllm/multimodal/processing.py)
- [vLLM 0.14 编码缓存](https://github.com/vllm-project/vllm/blob/v0.14.0/vllm/v1/core/encoder_cache_manager.py) 与 [KV 块哈希](https://github.com/vllm-project/vllm/blob/v0.14.0/vllm/v1/core/kv_cache_utils.py)
- [vLLM 0.14 缓存配置](https://github.com/vllm-project/vllm/blob/v0.14.0/vllm/config/cache.py) 与 [worker 内存预算](https://github.com/vllm-project/vllm/blob/v0.14.0/vllm/v1/worker/gpu_worker.py)
- [官方 0.6B 模型配置](https://huggingface.co/Qwen/Qwen3-ASR-0.6B/blob/main/config.json)
