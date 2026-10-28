# 原生 Qwen 验证工具

本目录是独立 uv 开发验证环境，不进入发布包。正式识别通过 ctypes PCM 接口；旧 HTTP 短样本评测保留作为历史实验。
依赖不包含 Torch / Transformers / qwen_asr；旧基准、转换依赖和锁文件仅在开发者本地归档，不在公开源码内，不维护旧运行环境。

```powershell
uv sync --project experiments/native-qwen
# 下载固定公开 GGUF / FLEURS 输入；不会下载旧 BF16 权重
uv run --project experiments/native-qwen python experiments/native-qwen/prepare_migration.py
# 原生质量/性能（输出目录必须尚不存在），GPU 测试串行执行
uv run --project experiments/native-qwen python experiments/native-qwen/evaluate_migration.py --size 0.6 --output data/verification/new-native-quality
uv run --project experiments/native-qwen python experiments/native-qwen/verify_native_contracts.py --size 1.7 --output data/verification/new-native-contracts
```

`soak_native.py` 验证真实节奏 1 秒 PCM 步进及长文件，不播放音频、不打开设备。
`compare_migration.py` 读取归档基线报告与原生报告，比较 CER/WER、热中位数/P95；不运行旧引擎。
`build_migration_runtime.py --root .` 对固定 CrispASR v0.8.45 源码应用 ABI、解码和 UTF-8 路径补丁，自动定位带 C++ CMake 工具的 Visual Studio，使用 work 中的 Vulkan SDK 构建。可用 `--vs-root` 显式指定 Visual Studio 安装目录。
源码提交、GGML 提交、模型 revision/SHA256、测试语料与结果见 `../../docs/native-vulkan-migration.md`；发布运行库清单见 `../../runtime/native/win-x64/runtime-manifest.json`。
SDK、上游源码与转换产物留在 work；运行包只包含白名单 DLL/许可证，不包含 SDK。
已有 work/native-qwen/qwenasr.cpp/checkpoints 联接指向原模型缓存，保留，不对实验树递归删除。
