# 源码与本地文件目录

## 持续维护的源码

| 目录 | 内容 |
|---|---|
| `src/livecaptions_asr/` | 识别、音频、分段、端点、字幕文本与原生模型 API |
| `packages/` | 采集、会议、翻译及桌面服务 API |
| `apps/desktop-winui/` | WinUI 前端源码与资源 |
| `apps/desktop-backend/` | JSONL 后台进程 |
| `apps/asr-test/` | 无界面 HTTP/API 组合验证应用 |
| `tests/`、各包 `tests/` | 核心、发布和各模块的有效测试 |
| `scripts/`、`installer/` | 模型下载、验证、便携/安装包构建 |
| `experiments/` | 仍可复用的原生构建、质量/性能和端点验证工具 |
| `runtime/native/` | 运行必需 DLL、固定版本清单和许可证 |
| `docs/`、`examples/` | 公开契约、构建说明及 API 示例 |

## 保留在本地、不进入源码发布的内容

- `.venv`：各项目独立 uv 环境，保留当前开发/测试所需依赖。
- `models/`：已下载权重及历史缓存。
- `data/`：配置、录音、会议、公开样本、历史验证报告。
- `dist/`：现有便携目录、压缩包和安装版。
- `work/`：原生构建使用的上游源码、SDK、编译器工具，以及历史实验的权重、语料和证据。含指向模型缓存的联接，不整目录递归清空。
- `plan/`、`archive/`：开发实施记录和旧源码/测试快照，仅本地保留。

`bin/obj`、Python 缓存、临时 publish 目录均可由对应构建命令重建，不属于业务源码。根 `.gitignore` 已统一排除 .NET 输出、IDE 状态和测试缓存。

## 2026-10-28 清理

移出重复的临时发布目录、旧界面验证二进制、候选运行文件、原生中间构建、临时验收环境、迁移一次性脚本及 WinUI `bin/obj`，并清理源码树中的解释器/测试缓存。项目目录减少约 4.21 GiB。当前原生运行库和构建源/SDK保持原位。

历史清理使用仓库外的本地恢复目录保存移出的文件，`restore-manifest.json` 记录原位置与新位置。恢复目录及其个人路径不纳入公开源码。

清理记录位于 `data/verification/source-cleanup-20261028/`。清理后 273 项 Python 测试、Ruff 和 WinUI 全新构建通过，原生 DLL、旧源码归档及安装包哈希一致。验证重建产物另存恢复目录的 `verification-rebuild/`，主源码目录保持无 `bin/obj`。

首次再次启动源码版时，`start.ps1` 会按现有流程重新编译 WinUI；已交付安装包和便携包可直接使用。
