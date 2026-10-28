# 桌面后端进程适配器

薄 JSON Lines 适配层，调用 `livecaptions_service` 库。由 WinUI 前端以隐藏子进程启动；不监听端口，也不依赖任何前端或 HTTP 测试应用。

```powershell
uv sync --project apps/desktop-backend
uv run --project apps/desktop-backend --no-sync python -u -m livecaptions_backend --root .
uv run --project apps/desktop-backend --no-sync pytest -q apps/desktop-backend/tests
```

标准输入/输出为 UTF-8 JSON Lines，stdout 专用于协议消息；Python 和原生依赖输出重定向至 stderr。请求、配置和密钥不写日志。stdin EOF 触发正常停止和保存；加载模型和推理在服务工作线程执行，控制接口仍可响应。

所有依赖安装在本目录 `.venv`，核心与共享包通过可编辑路径依赖复用。API 契约见 [desktop-api.md](../../docs/desktop-api.md)。

保留的真实验证脚本：`scripts/profile_startup.py` 检查同进程模型复用且不打开设备；`scripts/verify_api_protocols.py` 使用现有配置和合成测试句验证三种总结协议，不改变配置。

`scripts/verify_service.py` 将已有录音的 PCM 注入真实 `SessionRunner`，连续执行两次字幕会话，验证 Qwen / Silero / Smart Turn 与模型复用；不打开设备、不播放声音。报告写入 `data/verification/desktop-service-*/`。
