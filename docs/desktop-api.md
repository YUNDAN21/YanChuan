# 桌面服务与进程协议 v1

依赖方向：WinUI → 进程客户端 → `apps/desktop-backend` → `packages/desktop-service` → 识别 / 采集 / 会议 API。核心模型适配器不导入服务、设备、存储或前端。服务可直接作为 Python 库调用，进程接口只是传输适配。

## 通信

每行一个 UTF-8 JSON 对象，换行由 JSON 转义。父进程启动后先请求 `hello`：

```json
{"id":1,"method":"hello","params":{}}
{"type":"response","id":1,"result":{"protocol":1,"configuration":{},"snapshot":{}}}
{"type":"event","seq":1,"session_id":"session-id","name":"state","payload":{"state":"preparing","mode":"meeting","active":true}}
```

响应通过 ID 关联，设备枚举、API 模型查询和本地模型清单均可异步完成，响应可能乱序。清单检查不占用控制读取线程，停止指令可独立处理。错误为 `{"type":"response","id":1,"error":{"code":"request_failed","message":"..."}}`。事件顺序号在进程内单调递增；会话 ID 每次开始更新。快照包含当前顺序号，客户端按顺序应用事件，修订字幕原位更新而不是重复追加。

配置只通过父子管道提供给本地编辑器，不进入日志或导出。stdout 不是诊断输出；stderr 不承载请求。退出先请求 `shutdown` 并等待响应和进程结束；父进程异常关闭时 stdin EOF 同样收尾。

## 方法

| 方法 | 参数 / 返回 |
| --- | --- |
| `hello` | 协议版本、配置、状态快照；不加载模型 |
| `snapshot` | 活动任务、会议、字幕、总结状态及顺序号 |
| `devices.list` | 设备 ID、名称、kind；不打开录音 |
| `session.start` | `mode`: meeting / file / captions；`title`、`file`（仅 file）、`selection` |
| `session.stop` | 可选 `cancel`；默认停止采集并确认尾句 |
| `session.source` | `source`: system / microphone / both；`session_id`: 当前录音会话 ID；返回已确认音源、待切换音源和 switching |
| `meetings.list` | 已保存会议元数据与目录 |
| `meetings.open` | `directory`，只打开当前记录根目录下已有 SQLite 会议 |
| `meetings.rename` | `title`，非空单行名称；返回更新后的元数据，同步磁盘导出并保留目录 |
| `meetings.delete` | `directory`；永久删除指定会议目录，返回 id/title/directory/current；当前会议录音或总结中拒绝删除 |
| `meetings.content` | `kind`: all / transcript / summaries，返回 `text`，包含全部已确认内容 |
| `meetings.export` | 从 SQLite 重试写入 Markdown / JSON；返回 `success` 与 `error`，不调用 AI |
| `chat.list` | 当前会议的 `meeting_id`、按 ID 排序的 `messages`、上下文状态及可空的 `preview` |
| `chat.usage` | 可选 `question`（输入框草稿）；返回 meeting_id、estimated_tokens、reserved_tokens、total_tokens、context_tokens 和 over_budget，不调用 AI |
| `chat.compress` | `meeting_id`；后台压缩早期聊天，返回会议与请求 ID；保留最近两轮原始问答 |
| `chat.context.reset` | `meeting_id`；恢复完整聊天上下文，不删除消息，不改变会议原文 |
| `chat.send` | `question`；立即返回 `meeting_id`、`request_id`、`estimated_tokens`，推理在独立线程执行 |
| `chat.cancel` | 可选 `request_id`，停止当前会议的等待并丢弃迟到结果，本地 Agent 请求同时结束对应的 CLI 进程 |
| `chat.configure` | 部分更新：可选 `api_profile_id`、整数 `context_tokens`（最小 8192）、`model`、`reasoning_effort`；未传字段保持当前值 |
| `summary.request` | 是否有未总结内容；成功后保存并推进检查点 |
| `schedule.set` | 整数 `minutes` 1–120、`automatic` |
| `configuration.get` | 本地配置，含 API 密钥，勿记录 |
| `profiles.save` | `profiles` 列表和 `selected_id`，先校验再原子保存 |
| `profiles.select` | 配置 `id`；当前总结任务保持旧客户端快照 |
| `profiles.models` | `base_url`、`api_key`、`protocol`；返回去重排序的模型 ID 列表，允许使用未保存草稿，不需要填写 model |
| `profiles.agent_version` | `connection` 为 `codex` 或 `agy`，可选 `executable`；返回已安装 CLI 的版本，允许使用未保存草稿 |
| `profiles.agent_catalog` | `connection`、可选 `executable` 及 `include_models`，返回实际路径、CLI 默认模型、可选模型 ID 和名称，以及查询警告 |
| `templates.save` | `templates` 列表和 `selected_id`，草稿保存前不影响现有配置 |
| `templates.defaults` | 四种预置正文 |
| `appearance.set` | `mode`: light / dark |
| `window.configure` | 布尔值 `close_to_tray`；保存关闭主窗口后的行为，不改变录音或任务状态 |
| `captions.configure` | `enabled`、`display_mode`（original / bilingual / translated）、`target_language`（zh / ja / en）、可空 `api_profile_id` |
| `captions.watch` | 布尔值 `enabled`，订阅或关闭悬浮字幕；返回当前订阅状态 |
| `subtitle.split` | `text`、可用 `width`、前端实测 `glyph_widths`；返回保持原文的语义短行 |
| `shutdown` | 停止采集、等待尾句和当前总结、关闭存储，返回 closed |

`selection` 包含 `source`（system / microphone / both）、`language`（auto / zh / ja / en）、可空的 `microphone_id` / `system_id`。设备采集使用有界 PCM；文件由核心逐段解码与识别。

会议处于 recording 时可调用 `DesktopService.set_source(source, session_id=...)`，JSONL 对应 `session.source`。请求只排队，设备操作在采集线程进行；一次只允许一个待切换请求。新设备打开成功后发送确认事件，失败时返回原音源与错误，录音继续。加载、停止、文件导入或独立字幕状态拒绝此会议指令；会话 ID 防止过期请求影响下一场会议。切换保持同一会议、模型、识别流及时间轴，使用本次开始时选定的设备 ID。

配置项包含 `id`、`name`、`model` 和 `connection`。`connection` 默认 `http`，适用于已有配置；HTTP 项使用 `base_url`、`api_key` 与 `protocol`（`chat_completions` / `responses` / `anthropic`）。本地 Agent 项使用 `connection` 值 `codex` 或 `agy`，可选 `executable` 完整路径，留空从本机查找 CLI。总结和问答分别选择配置 ID。模板项包含 `id`、`name`、`prompt`。

`local_agent_catalog()` 使用 Codex 的 `config/read`、`model/list`，或 agy 的 `--print /model` 和 `models` 获取信息。返回的 `default_model` 供界面显示，配置中的空模型仍跟随 CLI。传 `include_models=false` 只查询实际路径和默认模型。

`LocalAgentClient` 通过现有的 `SummaryClient` 和 `ChatClient` 契约接入。每次模型调用按行读取独立进程的 UTF-8 JSON 事件。Codex 使用 App Server 的临时会话和 `item/agentMessage/delta`，agy 使用 headless 流的 `agent_response` 文本增量。`MeetingAgent` 从生成中的 JSON 提取回答正文，完成后核对引用并保存。字幕翻译使用 HTTP 连接。 [Codex App Server](https://learn.chatgpt.com/docs/app-server) [agy Headless 模式](https://antigravity.google/docs/cli/headless/)

`configuration.close_to_tray` 默认 false，持久化在不含密钥的 `data/meeting-appearance.json`，与主题相互保留。开启后关闭主窗口只隐藏到托盘，录音和已打开的字幕窗口继续运行。托盘点击/双击恢复主窗口，右键提供“打开言川”和“退出”；显式退出调用原有后端收尾流程，停止采集、保存尾句并释放模型后关闭窗口和托盘。托盘由 WinUI 层基于已有 WinUIEx 实现，Python 仅保存偏好，不控制 Windows 通知区域。

## 事件与生命周期

- `state`：idle / preparing / recording / draining / summarizing / ended / error，附带 mode 和 active。同一时刻只有一个转写任务。
- `session.source`：`source` 为实际音源，`requested_source` 为待切换音源（无请求时 null），`switching` 为切换状态，失败时附 `error`。按事件所属 session_id 应用，完成事件可能早于请求响应。快照中以 `audio_source` 提供同样的状态字段。
- `progress`：核心启动阶段、状态及秒数；`file.progress`：已处理秒数与块数。
- `meeting.loaded`：元数据、目录、完整转写和总结；`transcript`：新增确认记录。
- `meeting.renamed`：更新后的会议元数据；`chat.updated`：`meeting_id`、全部消息和 `context` 状态。聊天事件归属以 payload 的会议 ID 为准，外层 session_id 仍属于当前识别会话。
- `chat.preview` 提供 `meeting_id`、`request_id`、`revision` 和当前已生成的 `text`，只用于展示正在回答的请求。切换会议时可从 `chat.list` 恢复该预览。任务完成、失败或取消后，以 `chat.updated` 的最终状态为准。
- `meeting.deleted`：id/title/directory/current，删除成功后移除历史项；若删除的是当前会议，清空转写、总结、问答和会议名称，后端回到 idle。
- `captions`：新增确认语义段 `confirmed` 和可空 `pending`。修订按段 ID 处理，仅 sentence 提交历史。文件同样使用核心语义事件投影，不通过前端再判边界。
- `captions.reset`：新任务开始时清空上一任务字幕和译文；随后按新任务事件增量填充。
- `summary.summary`、`summary.busy`、`summary.error`：独立总结状态；失败不推进检查点。
- `summary.export`：磁盘导出警告，空字符串表示已成功导出。SQLite 成功提交总结和检查点后，导出失败仍推送 `summary.summary`；不作为总结失败重试。`meeting.loaded` 与快照中的会议字段包含 `export_error`。
- `configuration`、`appearance`：成功保存后的配置或主题；`error`：安全错误消息；`finished`：本次转写和收尾完成。
- `translation.units`：新增语义短行与移除的临时行键；`translation`：按 key/source_text/target_language 关联的译文、ready/error/skipped 状态；`translation.reset`：语言或服务改变后的旧结果失效；`translation.status`：独立配置或网络状态。快照包含 translation_units、translations 与 translation_state。

重新连接启动一个空闲后端，不自动录音。原始转写实时提交 SQLite；历史打开时重建 Markdown 导出。关闭或取消时保留已确认记录，总结失败可手动重试。

停止指令通过采集器的非阻塞 `request_stop()` 立即固定采集截止点，音频设备在所属线程收尾；已接收音频继续完成识别。关闭自动总结时，停止只等待已提交的手动总结，不为新尾句新增请求；自动模式则总结最终新增内容。

会议与文件任务持续投影字幕短行；开启悬浮字幕订阅后才提交对应翻译，关闭订阅会作废尚未完成的结果。独立字幕任务按字幕偏好直接翻译。前端重连后重新订阅，双语模式在新译文等待期间显示新原文；仅译文模式不回退显示原文。

## 聊天历史压缩与会议查阅

点击问答输入框的上下文圆环，可查看固定上下文、聊天历史和原文规模，执行“保存并压缩”，查看聊天摘要或恢复完整历史。手动会议时间范围设置及接口已删除。

`ChatCoordinator.compress(directory, meeting_id, client, context_tokens=...)` 仅压缩较早的已完成问答，最近两轮原样保留。使用提交时的问答模型与独立压缩提示词；会议原文不进入历史压缩请求。成功且估算占用减少后，原子保存摘要和消息检查点，原聊天消息仍完整留存。

`chat_context` 表持久化 summary、through_message、model、updated_at、status、request_id、error、revision；旧试用数据中的范围字段不再读取。状态为 idle / compressing / completed / failed / cancelled / interrupted。失败、取消和迟到结果不替换已有摘要。`chat.context.reset` 恢复原聊天上下文。

会议问答使用 `MeetingStore.reference_snapshot()` 获取提交时已确认的不可变原文快照，再由 `MeetingAgent` 查阅。`MeetingReference` 独立提供目录、Unicode 关键词搜索和分页读取，原文分片有稳定 ID、原记录 ID 和时间戳，不改变数据库、识别边界或导出。当前快照按会议标题及最后记录 ID 缓存，新问题才纳入新转写。

模型先选择局部查证或整场遍历。局部查证通过 directory / search / read / scan / answer 指令逐步获取资料；整场问题由宿主按顺序遍历每批原文，提取与问题有关的事实，完整覆盖后整理答案。旧工具结果退出下一轮输入，保留有界工作笔记及原文 ID，需要时重新读取。回答引用只接受已读取的原文编号，逐批回查后附时间戳。达到预算或请求上限时明确失败，不静默漏过材料。

该循环使用跨协议 JSON 文字动作契约，复用 Chat Completions / Responses / Anthropic 的现有消息传输，不要求服务支持某家的原生工具调用格式；模型须按提示输出结构化 JSON。解析失败有明确错误，不把工具指令当回答显示。只允许会议只读工具，无文件路径、Shell 或外部网络工具。

`chat.updated` 除消息和 context 外，还包含 activity、activity_request_id，显示搜索、读取、遍历进度及回查状态。前端只为当前会议的待处理请求更新状态。`chat.cancel` 同时停止当前查阅的后续模型请求，丢弃正在等待的迟到结果；切换会议后结果仍归属原会议。

`chat.usage` 返回 meeting_tokens（索引）、history_tokens、question_tokens、source_tokens（完整本地原文规模）、tool_tokens（查阅预留）、retrieval_mode、can_compress、compressed 等。完整原文不计入固定上下文，每次实际模型请求另做输入预算检查。容量仍按 token 估算，默认预留回答 4096、查阅 2048；超限提示压缩聊天历史或提高模型容量。

## 分组笔记

`livecaptions_meetings.NoteStore(path)` 为独立 SQLite 笔记 API，支持分组及笔记 CRUD。
`livecaptions_service.NotesService(root)` 组合笔记库与会议来源查询，无界面、无模型请求。
`DesktopService.note_call(action, **params)` 在服务控制锁下转发并推送 `notes.changed`（payload 为 action），JSONL 接口如下：

| 方法 | 参数 | 返回 |
| --- | --- | --- |
| `notes.groups` | 无 | 分组列表，含 id/title/meeting_id/note_count |
| `notes.group.create` | title | 新分组 |
| `notes.group.rename` | group_id, title | 更新后的分组 |
| `notes.group.delete` | group_id | true，级联删除组内笔记 |
| `notes.meeting_group` | meeting_id | 获取或创建会议对应组 |
| `notes.list` | group_id | 完整笔记列表 |
| `notes.create` | group_id, title?, content? | 新笔记 |
| `notes.update` | note_id, title, content | 更新后的笔记 |
| `notes.delete` | note_id | true |
| `notes.from_chat` | meeting_id, request_id | 将已完成的 assistant 回答保存为笔记 |

笔记包含 id、group_id、title、content、source_request_id、created_at、updated_at。
正文保持原始 Markdown；手动笔记 source_request_id 为 null。摘录只从指定会议的持久化问答读取，界面不上传重新拼装的回复，也不依赖当前打开会议。
同一会议组内相同 request_id 只保留一份摘录，再次添加不会覆盖编辑。会议和笔记组分别命名；组创建时采用会议名称，之后可独立改名。

数据独立保存于 `data/notes.sqlite3`，首次使用自动建表，不迁移或改动旧会议数据库。
删除会议保留笔记；删除组只影响该组笔记。笔记不进入问答模型上下文，不影响转写、总结游标和会话互斥。

## 会议问答

`ChatClient`、`ChatHistory`、`ChatCoordinator` 位于会议库，不依赖桌面服务、识别或 UI。会议问答通过注入的 MeetingAgent 执行查阅，普通 ChatClient 调用仍可独立使用。只有成功完成的历史问答纳入后续上下文；启用压缩后使用早期摘要及未压缩轮次。

聊天独立选择已有 API；首次发送时将当前选择固定为聊天配置，之后切换总结配置不改变聊天服务。现有三协议均支持 user/assistant 多轮消息，Responses 使用本地完整历史和 store=false。当前请求固定客户端与会议快照；切换配置仅影响下一任务，继续录音产生的新条目在下次提问纳入。每个会议同时一个问题，不同会议可以分别处理；取消立即更新状态，HTTP 请求可能持续至响应或超时，但迟到结果被丢弃。

问答模型与思考强度持久化为 `chat_model`（空值跟随所选连接的模型）和 `chat_reasoning_effort`，不改变总结或翻译配置。强度支持 default / none / low / medium / high / xhigh / max；default 不向服务添加参数。Chat Completions 使用 `reasoning_effort`，Responses 使用 `reasoning.effort`；Anthropic 的 none 使用 `thinking.type=disabled`，其他显式档位使用 `output_config.effort`。各档位的模型支持情况由服务决定，不自动重试或降低档位。切换聊天连接且未指定模型时清除旧模型覆盖；只调整容量时保留模型和强度。

SQLite `chat_message` 表按需创建，不改已有转写和总结。消息包含 id、request_id、role、text、status、error、model、created_at、through_entry；状态为 completed/pending/failed/cancelled/interrupted。只保存用户问题和最终回答，不保存提示词、密钥或服务原始错误正文。独立连接允许在界面切换会议后向原会议保存回答；进程重新打开记录时将遗留 pending 标记为 interrupted。

## 删除会议

会议库提供 `meeting_for_deletion(root, directory)` 和 `delete_meeting(root, directory)`，只接受显式会议根目录的一个直属会议目录，拒绝根目录本身、外部路径及含符号链接/junction 的目录，使用会议 SQLite 确认记录归属。删除覆盖该目录内的转写、总结、问答数据库及导出文件，不处理模型、配置或外部音频文件。

服务负责先结束所属总结工作线程并关闭存储，调用 `ChatCoordinator.forget` 释放该会议所有等待中或已取消的聊天连接，迟到回复不再写入或发送事件。当前会议仍在采集或总结时要求先完成任务。WinUI 历史项右侧垃圾桶打开确认框，取消为默认选项；删除当前会议时清空内容，删除其他历史项时保留当前内容。删除失败显示错误，并在数据库仍存在时恢复当前记录。

## 字幕翻译组合

`livecaptions_translation.TranslationWorker` 是独立无网络调度 API，桌面服务注入具体 `ApiTranslator`。翻译仅消费实时字幕的语义短行，不改变识别边界，也不改写原文。部分字幕合并修订、确认文本复用缓存、切换语言/服务忽略旧请求结果。服务关闭时结束工作线程；普通停止识别不等待翻译。

字幕偏好单独保存到 `data/caption-settings.json`。`api_profile_id=null` 跟随当前 API，否则使用同一配置库中的独立项目。只需译文时前端不回退显示原文。透明观看是前端显示状态，不通过后端改变录音或翻译。


## 原生模型管理与释放

`models.list` 返回 `settings`、`assets`、`sources=["huggingface"]`、`runtime="crispasr-0.8.45-lc1"` 和下载状态。
`models.configure` 接受 `selected_model`（qwen-0.6b / qwen-1.7b）、`source`、可选绝对 `directory` 和 `model_file`（兼容 Q8 GGUF）；活动会话期间拒绝切换。
`models.download` 在独立进程下载选定模型和分段模型；`models.cancel` 停止下载，GGUF 分段文件保留供重试续传，大小与格式检查通过后才原子替换。GGUF 检查架构、张量边界、型号和精度，ONNX 检查固定大小与 CPU 运行时输入接口；不计算文件哈希。ONNX 就绪检查按路径、大小和修改时间缓存。
旧 selected_model/directory 保留；旧 ModelScope 选择映射到当前唯一有效 Hugging Face 源，旧 safetensors 缓存保持原位。
模型管理不会修改会议、API 密钥、模板、翻译或外观文件。模型切换和服务 close 显式释放原生权重。

后台默认加载包内 `backend/native/crispasr.dll`，开发模式加载 `runtime/native/win-x64/crispasr.dll`。
`--native-library` 可显式指定固定 ABI 运行库；`--device cpu` 显式使用 CPU，默认 Vulkan 独显。

推理设备持久化于 `data/model-settings.json` 的 `device` 字段：`cpu`、`vulkan` 或显式 `VulkanN`。
旧文件缺少该字段时使用 `vulkan`。`models.configure` 接受可选 `device`，不传则保留当前设备；
`models.list` 的 `settings.device` 返回当前进程实际选择（包含命令行覆盖）。
保存成功推送 `models.configured`，payload 为 `{ "device": "cpu" }` 等。
活动转写期间拒绝切换；空闲切换关闭已有识别器并释放模型，下一会话重新加载，GPU 失败不回退。
后台不指定 `--device` 时读取保存值；显式参数只覆盖当前进程，随后通过界面保存可持久化新的选择。

## 获取 API 模型列表

会议库 `list_api_models(ModelCatalogSettings(base_url, api_key, protocol))` 提供独立同步查询 API，不依赖模型、会议存储或界面。Chat Completions / Responses 使用兼容的 GET `/models` 与 Bearer 认证；Anthropic 使用 GET `/models`、`x-api-key`、`anthropic-version`，自动读取分页。

桌面服务 `api_models(...)` 消费当前编辑草稿，不保存配置或修改正在运行的总结、翻译和问答客户端。JSONL 在独立查询线程处理 `profiles.models`，查询期间仍能响应停止录音等控制命令。HTTP 错误仅返回状态码与提示，不回传服务响应正文或密钥，也不跟随重定向。

模型列表、总结、问答和翻译共用应用 `User-Agent` 与 `Accept: application/json`。各协议认证头保持不变：兼容 OpenAI 的协议使用 Bearer，Anthropic 使用自己的密钥及版本头。HTTP 401 显示认证失败；403 的 HTML/挑战响应显示网关防护提示，其他 403 显示权限及访问规则提示。诊断不读取或展示错误响应正文。

WinUI 的“API 配置”中点击“获取模型列表”，随后在模型框选择或输入关键词筛选；仍可手动填写模型 ID。修改地址、密钥、协议或切换配置会清空旧列表，并忽略旧请求的迟到结果。获取失败/列表为空时显示原因，已填写的模型保留；仅“保存并使用”持久化草稿。
会议问答的用量接口会按实际策略返回 `retrieval_mode`：`false` 表示完整会议原文与聊天历史能在配置容量及回答预留内一次提交；`true` 表示需要按需查阅。没有独立的会议时长或 32k 原文门槛。`chat.send` 返回的 `estimated_tokens` 与同一问题的 `chat.usage` 一致；`tool_tokens` 在完整提交时为 0。

问答/历史压缩默认 180 秒任务时限，期间 `chat.updated` 带当前阶段与已等待秒数；超时保存失败状态，可以重新发送，原记录和总结检查点保留。API 日志位于应用数据目录的 `data/desktop-backend/stderr.log`，新增请求阶段、耗时、正文块计数及 token 数字，不包含请求或响应正文。

本地 Agent 的诊断日志使用同一请求 ID 串起问答阶段、CLI 类型、首次正文耗时、总耗时、增量数、退出码及失败类别。言川只记录诊断字段，CLI stderr 用于本地归类错误。
