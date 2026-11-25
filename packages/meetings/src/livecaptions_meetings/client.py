"""JSON summary transport; wire formats are independent adapters."""

import json
import logging
import math
import re
from dataclasses import dataclass, field
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .errors import SummaryError
from .http_transport import ProviderHttpError, provider_opener, request_headers
from .local_agent import LocalAgentClient, LocalAgentSettings
from .prompts import SUMMARY_SYSTEM_PROMPT, summary_input
from .protocols import endpoint_url, validate_api_url, wire_protocol
from .store import TranscriptEntry

logger = logging.getLogger(__name__)


def response_diagnostic(payload):
    """Only numeric counters and known protocol labels; never persist provider text."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("output_tokens_details", usage.get("completion_tokens_details", {}))
    details = details if isinstance(details, dict) else {}
    output = usage.get("output_tokens", usage.get("completion_tokens"))
    reasoning = details.get("reasoning_tokens")
    input_details = usage.get("input_tokens_details", usage.get("prompt_tokens_details", {}))
    input_details = input_details if isinstance(input_details, dict) else {}
    cached = input_details.get(
        "cached_tokens", usage.get("prompt_cache_hit_tokens", usage.get("cache_read_input_tokens"))
    )
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    items = payload.get("output", []) if isinstance(payload, dict) else []
    items = items if isinstance(items, list) else []
    messages = [item for item in items if isinstance(item, dict) and item.get("type") == "message"]
    blocks = [
        block
        for item in messages
        for block in (item.get("content") if isinstance(item.get("content"), list) else [])
        if isinstance(block, dict)
    ]
    return {
        "output_tokens": output if type(output) is int and output >= 0 else None,
        "reasoning_tokens": reasoning if type(reasoning) is int and reasoning >= 0 else None,
        "input_tokens": input_tokens if type(input_tokens) is int and input_tokens >= 0 else None,
        "cached_tokens": cached if type(cached) is int and cached >= 0 else None,
        "messages": len(messages),
        "text_blocks": sum(block.get("type") == "output_text" for block in blocks),
        "reasoning_items": sum(
            isinstance(item, dict) and item.get("type") == "reasoning" for item in items
        ),
    }


def read_response(response, timeout, started):
    """A provider sending periodic bytes cannot extend a request indefinitely."""
    chunks, size = [], 0
    while True:
        if monotonic() - started >= timeout:
            raise TimeoutError()
        chunk = response.read1(min(64 * 1024, 4 * 1024 * 1024 + 1 - size))
        if monotonic() - started >= timeout:
            raise TimeoutError()
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > 4 * 1024 * 1024:
            raise SummaryError("AI 服务响应超过大小限制，本次未更新进度。")


@dataclass(frozen=True)
class SummarySettings:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 90
    system_prompt: str = SUMMARY_SYSTEM_PROMPT
    protocol: str = "chat_completions"
    reasoning_effort: str = "default"

    def __post_init__(self):
        validate_api_url(self.base_url)
        wire_protocol(self.protocol)
        if (
            not self.model.strip()
            or not self.api_key.strip()
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("总结模型、密钥和超时时间需要有效配置")
        if not self.system_prompt.strip():
            raise ValueError("总结提示词不能为空")
        if self.reasoning_effort not in {
            "default",
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("不支持的思考强度")


class HttpSummaryClient:
    def __init__(self, settings: SummarySettings):
        self.settings = settings
        self.wire = wire_protocol(settings.protocol)
        self._opener = provider_opener()

    @property
    def model(self):
        return self.settings.model

    def summarize(self, title: str, entries: list[TranscriptEntry], previous_summary="") -> str:
        return self.generate(summary_input(title, entries, previous_summary))

    def generate(self, content: str) -> str:
        """Generate text with the configured protocol and prompt, without meeting wrapping."""
        return self._request(self.wire.body(self.settings, content))

    def chat(self, messages: list[dict[str, str]]) -> str:
        """Stateless multi-turn transport; meeting context and scheduling belong to callers."""
        return self._request(self.wire.conversation_body(self.settings, messages))

    def _request(self, body) -> str:
        started = monotonic()
        logger.info("ai request started protocol=%s", self.settings.protocol)
        try:
            return self._perform_request(body, started)
        except SummaryError as exc:
            logger.warning(
                "ai request failed protocol=%s error_type=%s elapsed=%.3fs",
                self.settings.protocol,
                type(exc).__name__,
                monotonic() - started,
            )
            raise
        finally:
            logger.info(
                "ai request ended protocol=%s elapsed=%.3fs",
                self.settings.protocol,
                monotonic() - started,
            )

    def _perform_request(self, body, started) -> str:
        request = Request(
            endpoint_url(self.settings.base_url, self.wire.endpoint),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=request_headers(self.wire.headers(self.settings), json_body=True),
        )
        try:
            with self._opener.open(request, timeout=self.settings.timeout_seconds) as response:
                raw = read_response(response, self.settings.timeout_seconds, started)
                payload = json.loads(raw)
        except HTTPError as exc:
            error = ProviderHttpError(exc)
            exc.close()
            raise error from None
        except (URLError, TimeoutError, OSError):
            raise SummaryError("AI 服务连接失败或超时，请重试或切换模型。") from None
        except (ValueError, UnicodeError):
            raise SummaryError("总结服务返回了无法解析的响应") from None
        counters = response_diagnostic(payload)
        logger.info(
            "ai response received protocol=%s bytes=%d messages=%d text_blocks=%d "
            "reasoning_items=%d output_tokens=%s reasoning_tokens=%s input_tokens=%s "
            "cached_tokens=%s",
            self.settings.protocol,
            len(raw),
            counters["messages"],
            counters["text_blocks"],
            counters["reasoning_items"],
            counters["output_tokens"],
            counters["reasoning_tokens"],
            counters["input_tokens"],
            counters["cached_tokens"],
        )
        text = self.wire.extract(payload)
        if not isinstance(text, str) or not text.strip():
            logger.warning(
                "ai response empty protocol=%s output_tokens=%s reasoning_tokens=%s",
                self.settings.protocol,
                counters["output_tokens"],
                counters["reasoning_tokens"],
            )
            if counters["reasoning_tokens"] and (
                counters["output_tokens"] == counters["reasoning_tokens"]
            ):
                raise SummaryError("模型仅返回推理过程，没有生成正文；请降低思考强度或切换模型。")
            raise SummaryError("API 返回成功状态但正文为空，请重试或检查渠道的模型/协议配置。")
        text = re.sub(r"&(?:\#x0*20|\#0*32|nbsp);", " ", text, flags=re.IGNORECASE)
        return text.strip().replace(self.settings.api_key, "[redacted]")


class ChatSummaryClient(HttpSummaryClient):
    """Backward-compatible entry point; default settings use Chat Completions."""


def create_summary_client(
    settings: SummarySettings | LocalAgentSettings,
) -> HttpSummaryClient | LocalAgentClient:
    if isinstance(settings, LocalAgentSettings):
        return LocalAgentClient(settings)
    return HttpSummaryClient(settings)
