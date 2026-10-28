"""Stateless summary wire protocols; no network, capture, persistence or UI dependencies."""

from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from .errors import SummaryError

PROTOCOL_LABELS = {
    "chat_completions": "OpenAI Chat Completions",
    "responses": "OpenAI Responses",
    "anthropic": "Anthropic Messages",
}


def validate_api_url(base_url: str):
    parsed = urlsplit(base_url.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("总结 API 地址无效，请填写不含密钥或查询参数的 HTTP(S) 地址")
    try:
        _ = parsed.port
    except ValueError:
        raise ValueError("总结 API 端口无效") from None


def endpoint_url(base_url: str, endpoint: str) -> str:
    """Accept a base URL or a complete supported endpoint, keeping gateway prefixes."""
    validate_api_url(base_url)
    parsed = urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    for suffix in ["/chat/completions", "/responses", "/messages"]:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, (path or "/v1") + endpoint, "", ""))


def object_response(value):
    if not isinstance(value, dict):
        raise SummaryError("总结服务返回了无效的响应结构")
    return value


def text_blocks(value, allowed_types):
    if not isinstance(value, list):
        raise SummaryError("总结响应缺少有效的文字内容")
    parts = []
    for block in value:
        block = object_response(block)
        if block.get("type") == "refusal":
            raise SummaryError("总结服务拒绝了本次请求，本次未更新总结进度。")
        if block.get("type", "text") in allowed_types:
            text = block.get("text")
            if not isinstance(text, str):
                raise SummaryError("总结响应的文字内容无效")
            parts.append(text)
    return "\n".join(parts)


class SummaryWireProtocol(Protocol):
    endpoint: str

    def headers(self, settings) -> dict: ...
    def body(self, settings, content: str) -> dict: ...
    def conversation_body(self, settings, messages: list[dict[str, str]]) -> dict: ...
    def extract(self, response) -> str: ...


class ChatCompletionsProtocol:
    endpoint = "/chat/completions"

    def headers(self, settings):
        return {"Authorization": "Bearer " + settings.api_key}

    def body(self, settings, content):
        return {
            "model": settings.model,
            "stream": False,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": settings.system_prompt},
                {"role": "user", "content": content},
            ],
        }

    def conversation_body(self, settings, messages):
        body = self.body(settings, "")
        body["messages"] = [{"role": "system", "content": settings.system_prompt}, *messages]
        if settings.reasoning_effort != "default":
            body["reasoning_effort"] = settings.reasoning_effort
        return body

    def extract(self, response):
        choices = object_response(response).get("choices")
        if not isinstance(choices, list) or not choices:
            raise SummaryError("总结响应缺少有效的文字内容")
        choice = object_response(choices[0])
        if choice.get("finish_reason") == "length":
            raise SummaryError("总结输出达到长度限制，本次未更新进度，请缩短总结间隔后重试。")
        if choice.get("finish_reason") not in {None, "stop"}:
            raise SummaryError("总结未正常完成，本次未更新总结进度。")
        message = object_response(choice.get("message"))
        if message.get("refusal") or message.get("tool_calls"):
            raise SummaryError("总结未返回完整正文，本次未更新总结进度。")
        content = message.get("content")
        return text_blocks(content, {"text"}) if isinstance(content, list) else content


class ResponsesProtocol:
    endpoint = "/responses"

    def headers(self, settings):
        return {"Authorization": "Bearer " + settings.api_key}

    def body(self, settings, content):
        return {
            "model": settings.model,
            "stream": False,
            "store": False,
            "max_output_tokens": 4096,
            "instructions": settings.system_prompt,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": content}]}],
        }

    def conversation_body(self, settings, messages):
        body = self.body(settings, "")
        body["input"] = messages
        if settings.reasoning_effort != "default":
            body["reasoning"] = {"effort": settings.reasoning_effort}
        return body

    def extract(self, response):
        response = object_response(response)
        if response.get("status") == "incomplete":
            raise SummaryError("总结输出不完整，本次未更新总结进度。")
        if response.get("status") != "completed" or response.get("error"):
            raise SummaryError("总结未正常完成，本次未更新总结进度。")
        output = response.get("output")
        if not isinstance(output, list):
            raise SummaryError("总结响应缺少有效的文字内容")
        parts = []
        for item in output:
            item = object_response(item)
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message" or item.get("role") != "assistant":
                raise SummaryError("总结返回了非正文内容，本次未更新总结进度。")
            if item.get("status", "completed") != "completed":
                raise SummaryError("总结输出不完整，本次未更新总结进度。")
            parts.append(text_blocks(item.get("content"), {"output_text"}))
        return "\n".join(parts)


class AnthropicMessagesProtocol:
    endpoint = "/messages"

    def headers(self, settings):
        return {"x-api-key": settings.api_key, "anthropic-version": "2023-06-01"}

    def body(self, settings, content):
        return {
            "model": settings.model,
            "stream": False,
            "max_tokens": 4096,
            "system": settings.system_prompt,
            "messages": [{"role": "user", "content": content}],
        }

    def conversation_body(self, settings, messages):
        body = self.body(settings, "")
        body["messages"] = messages
        if settings.reasoning_effort == "none":
            body["thinking"] = {"type": "disabled"}
        elif settings.reasoning_effort != "default":
            body["output_config"] = {"effort": settings.reasoning_effort}
        return body

    def extract(self, response):
        response = object_response(response)
        if response.get("stop_reason") in {"max_tokens", "model_context_window_exceeded"}:
            raise SummaryError("总结输出不完整，本次未更新总结进度。")
        if response.get("type") != "message" or response.get("stop_reason") not in {
            "end_turn",
            "stop_sequence",
        }:
            raise SummaryError("总结未正常完成，本次未更新总结进度。")
        return text_blocks(response.get("content"), {"text"})


def wire_protocol(name: str) -> SummaryWireProtocol:
    factories = {
        "chat_completions": ChatCompletionsProtocol,
        "responses": ResponsesProtocol,
        "anthropic": AnthropicMessagesProtocol,
    }
    if name not in factories:
        raise ValueError("不支持的总结 API 协议")
    return factories[name]()
