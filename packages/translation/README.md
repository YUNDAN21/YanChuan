# 独立字幕翻译 API

纯标准库包，提供 `Translator` 协议、不可变 `TranslationUnit` / `TranslationResult` 与 `TranslationWorker`。不导入识别、会议、设备、HTTP 或任何前端；具体翻译服务由调用方注入。

```python
from livecaptions_translation import TranslationUnit, TranslationWorker

# translator 实现 translate(text, source_language, target_language) -> str
worker = TranslationWorker(translator, "zh", on_update=on_translation)
worker.submit(TranslationUnit("session:sentence:cue", "Hello world.", 1.2, "English"))
# 部分字幕反复修订同一个 key，只翻译最新文本；确认后可复用相同文字的译文。
worker.reset_pending()
worker.configure(other_translator, "ja")  # 旧请求结果自动失效
worker.close()
```

一个后台消费者处理请求，部分字幕去抖并合并，确认字幕队列默认最多 32 条。积压时较早待译项返回 skipped 状态，保留原文并优先新字幕；不阻塞采集和识别。相同文本缓存最多 512 项。错误以独立结果返回，后续字幕继续翻译。观察者不在队列锁内调用。

桌面应用的 `ApiTranslator` 位于 `packages/desktop-service/`，组合已有 Chat Completions / Responses / Anthropic 适配器，只发送翻译提示词及原文，不使用会议总结提示词。密钥由应用层配置并脱敏。

```powershell
uv sync --project packages/translation
uv run --project packages/translation --no-sync pytest -q packages/translation/tests
```
