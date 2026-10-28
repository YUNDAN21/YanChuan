# LiveCaptions Meetings API

可独立调用的会议记录、时间戳、持久化和定时总结包。只依赖 Python 标准库，不导入 Qt、ASR、设备或测试应用。

`chat_context_usage(context, history, question="", context_tokens=272000)` 使用与发送相同的上下文构建及 token 估算，返回输入估算、回答预留、总预算占用及超限状态；空草稿且有待处理请求时计入该请求问题。该接口不调用 AI、不推进总结检查点。

`list_api_models(ModelCatalogSettings(base_url, api_key, protocol))` 可独立获取服务的模型 ID 列表，不需要预先选择模型或创建会议。支持 Chat Completions / Responses 的兼容列表接口及 Anthropic 分页；失败抛出不含密钥或服务原始正文的 `ModelCatalogError`。

```python
from pathlib import Path
from livecaptions_meetings import (
    MeetingStore,
    SummarySettings,
    SummaryWorker,
    create_summary_client,
    ApiProfile,
    ApiProfileLibrary,
)

store = MeetingStore.create(Path("data/meetings"), "项目周会")
client = create_summary_client(
    SummarySettings(
        base_url=base_url,
        model=model_name,
        api_key=local_api_key,
        protocol="responses",  # 也可选 chat_completions 或 anthropic
        # 可选：替换默认的完整系统提示词。
        system_prompt="请结合上下文纠正转写错词，按主题总结并保留关键时间范围。",
    )
)
worker = SummaryWorker(store, client, interval_seconds=300, automatic=True)
worker.start()

# source_key 来自调用方，用于防止重复提交识别事件。
store.append("session:sentence:0", 10.2, 18.5, "我们决定周五完成测试。")
worker.request()  # 也可手动触发

# 停止输入后，处理尚未总结的尾段。
store.set_status("ended", ended=True)
worker.close(drain=True)
worker.wait()
store.close()
```

`MeetingStore` 使用 SQLite WAL/完整同步事务作为原始记录，确认转写逐条提交；生成 `transcript.md`、`summaries.md` 和 `meeting.json`。重新打开 `MeetingStore(directory)` 后调用 `export()` 可从数据库重建导出。

`SummaryWorker` 在独立线程执行。按时间触发，没有新转写时不调用模型；`configure(seconds, automatic)` 可动态修改间隔。`request()` 合并重复请求；每次只提交成功检查点之后的新增记录，单次新增文本默认不超过 12000 字符，长积压分批处理。上一段总结最多取末尾 6000 字符作为上下文参考，历史总结全文持续保存在本地。

`save_summary` 在同一事务中保存结果和推进检查点。网络失败、空响应或输出截断时不推进检查点，可重新 `request()`；应用重启后也可重新构造 worker 处理未完成的范围。`summary_errors()` 读取脱敏错误历史。`on_update(kind, value)` 提供 busy / summary / error 通知。

`SummarySettings` 显式接收 API 地址、模型和密钥；不读取全局环境。默认协议为 `POST /chat/completions`，超时默认 90 秒。密钥从对象 repr、错误和模型返回文字中脱敏，不存入会议记录。

`create_summary_client(settings)` 组合通用 HTTP 传输和 `settings.protocol` 指定的独立协议适配器。`ChatSummaryClient` 保留为兼容入口，默认配置仍为 Chat Completions。三个协议分别封装请求/鉴权及正文解析，协议实现不依赖网络、保存、调度、识别或 Qt：

| protocol | 端点 | 请求与响应 |
| --- | --- | --- |
| `chat_completions` | `/chat/completions` | Bearer 鉴权，system/user messages，choices/message 正文 |
| `responses` | `/responses` | Bearer 鉴权，instructions/input，max_output_tokens；读取 output 内全部 assistant/output_text，跳过 reasoning |
| `anthropic` | `/messages` | x-api-key 与 anthropic-version 2023-06-01，system/messages，读取 text 内容块 |

Responses 请求关闭服务端存储，三种协议均采用非流式总结。输出截断、失败、拒绝、工具调用、空内容或无效结构不会推进进度。兼容基础地址、带代理前缀的地址或完整接口 URL，未带路径的域名补 `/v1`；不接受 URL 内账号、查询参数或片段，HTTP 不跟随重定向。现有通用调度无需了解协议类型。

`ApiProfile(id, name, base_url, model, api_key, protocol)` 和 `ApiProfileLibrary(profiles, selected_id)` 提供独立配置 API，支持 `add/update/remove/select`，保留稳定 ID 和至少一组配置。密钥从对象 repr 排除；使用 `library.selected.summary_settings(system_prompt)` 创建本次不可变设置，再交给客户端工厂和 `worker.set_client()`。配置文件与界面属于外层应用，库本身不读取磁盘配置。

实现依据：[OpenAI 官方文档：文本生成与 Responses](https://developers.openai.com/api/docs/guides/text)、[Responses 迁移与无状态请求](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)、[Anthropic 鉴权](https://platform.claude.com/docs/en/manage-claude/authentication)。

`SummarySettings(system_prompt=...)` 可指定完整的系统提示词，省略时使用公开常量 `SUMMARY_SYSTEM_PROMPT`。`worker.set_client(ChatSummaryClient(new_settings))` 可在运行时更新服务或提示词；当前任务（包括其分批请求）保留原客户端，下一任务使用新客户端，响应的模型记录和错误脱敏均使用该任务的客户端。

`PromptTemplate(id, name, prompt)` 与 `PromptTemplateLibrary(templates, selected_id)` 提供独立模板 API。`default_prompt_templates()` 返回通用整理、会议纪要、讲解笔记、简洁摘要四个预置；模板库支持 `add` / `update` / `remove` / `select`，修改名称不会改变 ID，至少保留一个。`library.selected.prompt` 可直接传入 `SummarySettings.system_prompt`，不依赖桌面、配置文件或识别模型。复制模板列表可作为独立草稿，修改不会影响原库。

摘要先依据上下文修正转写错词、术语和断句，再合并重复、理顺因果和步骤，直接呈现可读的整理结果，不输出“待核对”或错词对照。根据内容选择主题结构：讲解侧重思路/机制，会议侧重决定/行动；保留关键条件和数字，不新增负责人或截止日期。每个主题/逻辑段保留简洁时间范围，减少逐小句标注。编辑策略独立在 `prompts.py`，原始转写和已有总结记录保留原样。

时间戳来自上游转写提供的相对秒数，精度取决于该来源；本包不推算说话人或词级对齐。`timestamp()` 输出 `HH:MM:SS`。

安装和测试：

```powershell
uv sync --project packages/meetings
uv run --project packages/meetings --no-sync pytest -q packages/meetings/tests
```

测试不请求实际 AI、不打开设备、不播放声音。

## 改名、完整复制与会议问答

`store.rename(title)` 同步修改数据库、meeting.json 与两个 Markdown 的标题，目录不变。`store.content(kind)` 返回 all/transcript/summaries 三种可读文本，供复制或其他客户端复用；导出文件也使用同一内容构建逻辑。

```python
from livecaptions_meetings import CHAT_SYSTEM_PROMPT, ChatCoordinator

client = create_summary_client(profile.summary_settings(CHAT_SYSTEM_PROMPT))
chat = ChatCoordinator(on_update=handle_chat_update)
request = chat.send(
    store.directory, store.context_snapshot(), "最终决定是什么？", client, context_tokens=272000
)
# on_update 包含 meeting_id 与持久化消息；可切换会议，原回答仍归原会议。
messages = chat.list(store.directory)
chat.cancel(store.metadata["id"], request["request_id"])  # 停止等待并丢弃迟到回答
chat.close()
```

ChatClient 协议只要求 model 与 chat(messages)，无需真实 HTTP 即可验证。ChatHistory 单独连接会议 SQLite，按需建表兼容旧记录；ChatCoordinator 提供独立后台调度。完整原文、总结和已完成问答组成当前请求快照，失败轮次不参与下一次模型请求。默认 272k token 预算，不复用总结的 12000 字符批次；字符类别估算仅用于预算，实际 token 数依服务。相关接口和状态见 [桌面 API](../../docs/desktop-api.md)。

协议多轮格式依据 [OpenAI conversation state](https://developers.openai.com/api/docs/guides/conversation-state) 与 [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)。聊天和总结各自使用专用提示词，网络客户端复用协议和安全响应解析。
