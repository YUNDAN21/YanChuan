"""Read a provider's model catalogue without meeting state or a selected model."""

import json
from dataclasses import dataclass, field
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from .http_transport import http_error_message, provider_opener, request_headers
from .protocols import endpoint_url, wire_protocol


class ModelCatalogError(RuntimeError):
    """Safe, user-facing discovery error; never includes server bodies or credentials."""


@dataclass(frozen=True)
class ModelCatalogSettings:
    base_url: str
    api_key: str = field(repr=False)
    protocol: str = "chat_completions"

    def __post_init__(self):
        if not isinstance(self.api_key, str) or any(
            ord(character) < 32 or ord(character) > 126 for character in self.api_key
        ):
            raise ModelCatalogError("API 密钥包含无效字符，请重新填写。")


def list_api_models(settings: ModelCatalogSettings) -> list[str]:
    """Return unique model IDs; support OpenAI-compatible and Anthropic pagination."""
    url = endpoint_url(settings.base_url, "/models")
    wire = wire_protocol(settings.protocol)
    opener = provider_opener()
    headers = request_headers(wire.headers(settings))
    models, cursors = set(), set()
    deadline = monotonic() + 30
    cursor = None
    for _ in range(20):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ModelCatalogError("获取模型列表超时，请重试或手动填写模型。")
        query = {"limit": 1000} if settings.protocol == "anthropic" else {}
        if cursor is not None:
            query["after_id" if settings.protocol == "anthropic" else "after"] = cursor
        request = Request(url + ("?" + urlencode(query) if query else ""), headers=headers)
        try:
            with opener.open(request, timeout=min(15, remaining)) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ModelCatalogError("模型列表响应过大，请手动填写模型。")
                payload = json.loads(raw)
        except HTTPError as exc:
            error = ModelCatalogError(http_error_message(exc, "获取模型列表"))
            exc.close()
            raise error from None
        except (URLError, TimeoutError, OSError):
            raise ModelCatalogError("获取模型列表连接失败或超时，请检查地址后重试。") from None
        except (ValueError, UnicodeError):
            raise ModelCatalogError("模型列表响应不是有效 JSON。") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ModelCatalogError("API 返回的模型列表格式不正确，可手动填写模型。")
        for item in payload["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ModelCatalogError("API 返回的模型列表缺少有效模型 ID。")
            identifier = item["id"].strip()
            if not identifier or (settings.api_key and settings.api_key in identifier):
                raise ModelCatalogError("API 返回了无效的模型 ID。")
            models.add(identifier)
        if payload.get("has_more") is not True:
            return sorted(models, key=str.casefold)
        cursor = payload.get("last_id")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ModelCatalogError("模型列表分页信息无效，请手动填写模型。")
        cursors.add(cursor)
    raise ModelCatalogError("模型列表分页过多，请手动填写模型。")
