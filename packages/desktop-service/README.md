# 无界面的桌面服务 API

`livecaptions_service.DesktopService` 直接组合识别、采集和会议库，供命令行、桌面进程或其他 Python 调用者使用。没有 Qt、HTTP、WinUI 或进程协议依赖。

```python
from pathlib import Path
from livecaptions_service import DesktopService, InputSelection

service = DesktopService(Path.cwd(), observer=lambda event: print(event.name))
service.start("captions", selection=InputSelection(source="system", language="auto"))
# 由调用方决定运行多久；stop 收齐尾句，cancel=True 放弃待确认内容。
service.stop()
service.wait()
service.close()
```

`start("meeting", title=..., selection=...)` 保存会议；`start("file", title=..., file=...)` 逐段处理文件。一次只运行一个转写任务；模型按需加载，同进程复用。确认句和当前修订文字由 `CaptionHistory` 投影，短字幕拆分调用核心 `SubtitleSplitter`。

`snapshot()`、`list_meetings()`、`open_meeting()` 提供状态和历史；`summarize_now()`、`configure_schedule()` 提供独立总结调度。`set_api_profiles()`、`select_api_profile()`、`set_templates()` 和 `set_appearance()` 提供配置 API。`configuration()` 含本地密钥，只供受信任的设置编辑器使用，勿打印或导出。

`rename_meeting(title)` 和 `meeting_content(kind)` 提供不依赖剪贴板的改名与完整内容 API。`send_chat(question)`、`chat_messages()`、`cancel_chat(request_id)`、`configure_chat(api_profile_id, context_tokens)` 提供独立会议问答；可通过 chat_client_factory 注入客户端。聊天消息按会议保存在 SQLite，切换会议不影响原请求保存；chat.updated 的 payload 携带 meeting_id，展示层按该 ID 匹配。关闭服务时未完成问答标记 interrupted，旧请求的迟到回答丢弃。

观察者收到不可变 `ServiceEvent(sequence, session_id, name, payload)`。依赖可通过 runtime、observer 和 client_factory 注入；测试不需前端、HTTP、真实设备或模型。

```powershell
uv sync --project packages/desktop-service
uv run --project packages/desktop-service --no-sync pytest -q packages/desktop-service/tests
```
