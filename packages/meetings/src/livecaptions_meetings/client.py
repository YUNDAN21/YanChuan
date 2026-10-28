"""JSON summary transport; wire formats are independent adapters."""

import json
import math
import re
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import SummaryError
from .prompts import SUMMARY_SYSTEM_PROMPT
from .protocols import endpoint_url, validate_api_url, wire_protocol
from .store import TranscriptEntry


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


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpSummaryClient:
    def __init__(self, settings: SummarySettings):
        self.settings = settings
        self.wire = wire_protocol(settings.protocol)
        self._opener = build_opener(_NoRedirect())

    @property
    def model(self):
        return self.settings.model

    def summarize(self, title: str, entries: list[TranscriptEntry], previous_summary="") -> str:
        content = json.dumps(
            {
                "meeting_title": title,
                "previous_context": previous_summary[-6000:],
                "new_transcript": [entry.markdown() for entry in entries],
            },
            ensure_ascii=False,
        )
        return self.generate(content)

    def generate(self, content: str) -> str:
        """Generate text with the configured protocol and prompt, without meeting wrapping."""
        return self._request(self.wire.body(self.settings, content))

    def chat(self, messages: list[dict[str, str]]) -> str:
        """Stateless multi-turn transport; meeting context and scheduling belong to callers."""
        return self._request(self.wire.conversation_body(self.settings, messages))

    def _request(self, body) -> str:
        request = Request(
            endpoint_url(self.settings.base_url, self.wire.endpoint),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", **self.wire.headers(self.settings)},
        )
        try:
            with self._opener.open(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise SummaryError("总结服务响应超过大小限制，本次未更新进度。")
                payload = json.loads(raw)
        except HTTPError as exc:
            raise SummaryError(f"总结服务返回 HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError):
            raise SummaryError("总结服务连接失败或超时，转写已保存在本地，可重试总结。") from None
        except (ValueError, UnicodeError):
            raise SummaryError("总结服务返回了无法解析的响应") from None
        text = self.wire.extract(payload)
        if not isinstance(text, str) or not text.strip():
            raise SummaryError("总结模型返回空内容，本次未更新总结进度。")
        text = re.sub(r"&(?:\#x0*20|\#0*32|nbsp);", " ", text, flags=re.IGNORECASE)
        return text.strip().replace(self.settings.api_key, "[redacted]")


class ChatSummaryClient(HttpSummaryClient):
    """Backward-compatible entry point; default settings use Chat Completions."""


def create_summary_client(settings: SummarySettings) -> HttpSummaryClient:
    return HttpSummaryClient(settings)
