"""Shared HTTP identity and safe diagnostics for AI providers."""

from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, build_opener

from .errors import SummaryError

USER_AGENT = "YanChuan/0.2.0"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def provider_opener():
    """Provider credentials stay on the configured endpoint, including on redirects."""
    return build_opener(_NoRedirect())


class ProviderHttpError(SummaryError):
    """Safe HTTP diagnostic; retains only status and a locally generated message."""

    def __init__(self, error: HTTPError):
        super().__init__(http_error_message(error, "AI 服务"))


def request_headers(authentication: dict[str, str], *, json_body=False) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **authentication}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def http_error_message(error: HTTPError, operation: str) -> str:
    """Classify responses without displaying provider bodies or credentials."""
    content_type = error.headers.get("Content-Type", "").lower() if error.headers else ""
    challenge = error.headers.get("cf-mitigated", "") if error.headers else ""
    if error.code == 403 and ("text/html" in content_type or challenge == "challenge"):
        detail = "请求被网关或防护页面拦截，请检查服务端访问规则"
    elif error.code == 401:
        detail = "认证失败，请检查 API 密钥"
    elif error.code == 403:
        detail = "访问被拒绝，请检查 API 密钥权限及服务端访问规则"
    elif error.code in {404, 405} and operation == "获取模型列表":
        detail = "该 API 未提供模型列表接口，可手动填写模型"
    else:
        detail = "服务请求失败"
    return f"{operation}：{detail}（HTTP {error.code}）。"
