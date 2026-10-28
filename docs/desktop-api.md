# 桌面服务与进程协议 v1

依赖方向：WinUI → 进程客户端 → `apps/desktop-backend` → `packages/desktop-service` → 识别 / 采集 / 会议 API。核心模型适配器不导入服务、设备、存储或前端。服务可直接作为 Python 库调用，进程接口只是传输适配。

## 通信

每行一个 UTF-8 JSON 对象，换行由 JSON 转义。父进程启动后先请求 `hello`：

```json
{"id":1,"method":"hello","params":{}}
{"type":"response","id":1,"result":{"protocol":1,"configuration":{},"snapshot":{}}}
{"type":"event","seq":1,"session_id":"session-id","name":"state","payload":{"state":"preparing","mode":"meeting","active":true}}
```

响应通过 ID 关联，设备枚举可异步完成。错误为 `{"type":"response","id":1,"error":{"code":"request_failed","message":"..."}}`。事件顺序号在进程内单调递增；会话 ID 每次开始更新。快照包含当前顺序号，客户端按顺序应用事件，修订字幕原位更新而不是重复追加。

配置只通过父子管道提供给本地编辑器，不进入日志或导出。stdout 不是诊断输出；stderr 不承载请求。退出先请求 `shutdown` 并等待响应和进程结束；父进程异常关闭时 stdin EOF 同样收尾。

## 方法

| 方法 | 参数 / 返回 |
| --- | --- |
| `hello` | 协议版本、配置、状态快照；不加载模型 |
| `snapshot` | 活动任务、会议、字幕、总结状态及顺序号 |
| `devices.list` | 设备 ID、名称、kind；不打开录音 |
| `session.start` | `mode`: meeting / file / captions；`title`、`file`（仅 file）、`selection` |
| `session.stop` | 可选 `cancel`；默认停止采集并确认尾句 |
| `meetings.list` | 已保存会议元数据与目录 |
| `meetings.open` | `directory`，只打开当前记录根目录下已有 SQLite 会议 |
| `meetings.rename` | `title`，非空单行名称；返回更新后的元数据，同步磁盘导出并保留目录 |
| `meetings.delete` | `directory`；永久删除指定会议目录，返回 id/title/directory/current；当前会议录音或总结中拒绝删除 |
| `meetings.content` | `kind`: all / transcript / summaries，返回 `text`，包含全部已确认内容 |
| `chat.list` | 当前会议的 `meeting_id` 与按 ID 排序的 `messages`；旧记录首次打开时恢复中断状态 |
| `chat.usage` | 可选 `question`（输入框草稿）；返回 meeting_id、estimated_tokens、reserved_tokens、total_tokens、context_tokens 和 over_budget，不调用 AI |
| `chat.send` | `question`；立即返回 `meeting_id`、`request_id`、`estimated_tokens`，推理在独立线程执行 |
| `chat.cancel` | 可选 `request_id`，停止当前会议的等待；已取消请求的迟到结果不再保存 |
| `chat.configure` | 部分更新：可选 `api_profile_id`、整数 `context_tokens`（最小 8192）、`model`、`reasoning_effort`；未传字段保持当前值 |
| `summary.request` | 是否有未总结内容；成功后保存并推进检查点 |
| `schedule.set` | 整数 `minutes` 1–120、`automatic` |
| `configuration.get` | 本地配置，含 API 密钥，勿记录 |
| `profiles.save` | `profiles` 列表和 `selected_id`，先校验再原子保存 |
| `profiles.select` | 配置 `id`；当前总结任务保持旧客户端快照 |
| `profiles.models` | `base_url`、`api_key`、`protocol`；返回去重排序的模型 ID 列表，允许使用未保存草稿，不需要填写 model |
| `templates.save` | `templates` 列表和 `selected_id`，草稿保存前不影响现有配置 |
| `templates.defaults` | 四种预置正文 |
| `appearance.set` | `mode`: light / dark |
| `captions.configure` | `enabled`、`display_mode`（original / bilingual / translated）、`target_language`（zh / ja / en）、可空 `api_profile_id` |
| `subtitle.split` | `text`、可用 `width`、前端实测 `glyph_widths`；返回保持原文的语义短行 |
| `shutdown` | 停止采集、等待尾句和当前总结、关闭存储，返回 closed |

`selection` 包含 `source`（system / microphone / both）、`language`（auto / zh / ja / en）、可空的 `microphone_id` / `system_id`。设备采集使用有界 PCM；文件由核心逐段解码与识别。

配置中的 API 项包含 id、name、base_url、model、api_key、protocol；protocol 为 chat_completions / responses / anthropic。模板项包含 id、name、prompt。旧单配置、模板、外观文件和会议 SQLite 直接兼容。

## 事件与生命周期

- `state`：idle / preparing / recording / draining / summarizing / ended / error，附带 mode 和 active。同一时刻只有一个转写任务。
- `progress`：核心启动阶段、状态及秒数；`file.progress`：已处理秒数与块数。
- `meeting.loaded`：元数据、目录、完整转写和总结；`transcript`：新增确认记录。
- `meeting.renamed`：更新后的会议元数据；`chat.updated`：`meeting_id` 与全部消息。聊天事件归属以 payload 的会议 ID 为准，外层 session_id 仍属于当前识别会话。
- `meeting.deleted`：id/title/directory/current，删除成功后移除历史项；若删除的是当前会议，清空转写、总结、问答和会议名称，后端回到 idle。
- `captions`：新增确认语义段 `confirmed` 和可空 `pending`。修订按段 ID 处理，仅 sentence 提交历史。文件同样使用核心语义事件投影，不通过前端再判边界。
- `summary.summary`、`summary.busy`、`summary.error`：独立总结状态；失败不推进检查点。
- `configuration`、`appearance`：成功保存后的配置或主题；`error`：安全错误消息；`finished`：本次转写和收尾完成。
- `translation.units`：新增语义短行与移除的临时行键；`translation`：按 key/source_text/target_language 关联的译文、ready/error/skipped 状态；`translation.reset`：语言或服务改变后的旧结果失效；`translation.status`：独立配置或网络状态。快照包含 translation_units、translations 与 translation_state。

重新连接启动一个空闲后端，不自动录音。原始转写实时提交 SQLite；历史打开时重建 Markdown 导出。关闭或取消时保留已确认记录，总结失败可手动重试。

## 会议问答

`ChatClient`、`ChatHistory`、`ChatCoordinator` 位于会议库，不依赖桌面服务、识别或 UI。调用方提供 `MeetingStore.context_snapshot()` 和具体客户端；快照包含完整转写、所有总结和原文检查点。只加入成功完成的历史问答，失败/取消的轮次保留供查看和重试，不作为后续模型上下文。上下文没有自动裁剪或检索；预算采用字符类别的 token 估算，预留 4096 输出，超预算时明确提示修改容量，服务 tokenizer 才是实际依据。

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
`models.download` 在独立进程下载选定模型和分段模型；`models.cancel` 停止下载，GGUF 分段文件保留供重试续传，完整大小/SHA256 通过后才原子替换。
旧 selected_model/directory 保留；旧 ModelScope 选择映射到当前唯一有效 Hugging Face 源，旧 safetensors 缓存保持原位。
模型管理不会修改会议、API 密钥、模板、翻译或外观文件。模型切换和服务 close 显式释放原生权重。

后台默认加载包内 `backend/native/crispasr.dll`，开发模式加载 `runtime/native/win-x64/crispasr.dll`。
`--native-library` 可显式指定固定 ABI 运行库；`--device cpu` 显式使用 CPU，默认 Vulkan 独显。

## 获取 API 模型列表

会议库 `list_api_models(ModelCatalogSettings(base_url, api_key, protocol))` 提供独立同步查询 API，不依赖模型、会议存储或界面。Chat Completions / Responses 使用兼容的 GET `/models` 与 Bearer 认证；Anthropic 使用 GET `/models`、`x-api-key`、`anthropic-version`，自动读取分页。

桌面服务 `api_models(...)` 消费当前编辑草稿，不保存配置或修改正在运行的总结、翻译和问答客户端。JSONL 在独立查询线程处理 `profiles.models`，查询期间仍能响应停止录音等控制命令。HTTP 错误仅返回状态码与提示，不回传服务响应正文或密钥，也不跟随重定向。

WinUI 的“API 配置”中点击“获取模型列表”，随后在模型框选择或输入关键词筛选；仍可手动填写模型 ID。修改地址、密钥、协议或切换配置会清空旧列表，并忽略旧请求的迟到结果。获取失败/列表为空时显示原因，已填写的模型保留；仅“保存并使用”持久化草稿。
