"""Read provider events and forward only assistant text to the caller."""

import json
from itertools import chain

from .agent_process import AgentProcessError

CODEX_APP_SERVER = [
    "app-server",
    "--listen",
    "stdio://",
    "-c",
    'web_search="disabled"',
    "--disable",
    "shell_tool",
    "--disable",
    "unified_exec",
    "--disable",
    "hooks",
    "--disable",
    "plugins",
    "--disable",
    "apps",
    "--disable",
    "browser_use",
    "--disable",
    "computer_use",
    "--disable",
    "multi_agent",
    "--disable",
    "multi_agent_v2",
    "--disable",
    "image_generation",
    "--disable",
    "memories",
]


def _object(value):
    return value if isinstance(value, dict) else {}


def _events(pipe):
    for line in pipe.lines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(event, dict):
            yield event


def _send(pipe, value):
    pipe.send((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))


def agent_failure(label, diagnostic, reason="invalid_output"):
    # Diagnostics can contain private data. Only classify known phrases locally.
    diagnostic = diagnostic.lower()
    if any(
        word in diagnostic
        for word in ("authentication required", "not logged in", "401 unauthorized")
    ):
        return AgentProcessError("auth", f"请先在 {label} 命令行中登录，再重试")
    if any(
        word in diagnostic
        for word in ("rate limit", "usage limit", "429 too many requests", "resource_exhausted")
    ):
        return AgentProcessError("rate_limit", f"{label} 已达到用量或速率限制，请稍后重试")
    if "timed out" in diagnostic or "deadline exceeded" in diagnostic:
        return AgentProcessError("timeout", f"{label} 等待超时，请重试")
    return AgentProcessError(reason, f"{label} 未返回有效结果，请检查命令行登录与模型配置后重试")


def agy_answer(pipe, prompt, emit):
    _send(pipe, {"event": "user", "message": {"content": prompt}})
    pipe.close_input()
    result = {}
    for event in _events(pipe):
        if event.get("event") == "step_update":
            step = _object(event.get("step_update"))
            if step.get("step_type") == "agent_response":
                emit(step.get("text_delta"))
        elif event.get("event") == "result":
            result = _object(event.get("result"))
    code = pipe.wait()
    if code == 0 and result.get("status") == "SUCCESS":
        return result.get("response")
    error = str(result.get("error", "")) + pipe.stderr_tail.decode("utf-8", errors="replace")
    raise agent_failure(pipe.label, error, "exit" if code != 0 else "invalid_result")


class _CodexRpc:
    def __init__(self, pipe):
        self.pipe = pipe
        self.events = iter(_events(pipe))
        self.request_id = 0

    def notify(self, method):
        _send(self.pipe, {"method": method, "params": {}})

    def _failure(self, detail="", reason="rpc"):
        diagnostic = str(detail) + self.pipe.stderr_tail.decode("utf-8", errors="replace")
        return agent_failure(self.pipe.label, diagnostic, reason)

    def notifications(self):
        for event in self.events:
            if "id" in event and "method" in event:
                raise self._failure(reason="unexpected_server_request")
            yield event

    def call(self, method, params, queued=None):
        self.request_id += 1
        _send(self.pipe, {"id": self.request_id, "method": method, "params": params})
        for event in self.notifications():
            if event.get("id") == self.request_id:
                if "result" in event:
                    return _object(event["result"])
                raise self._failure(event.get("error"), f"rpc_{method}")
            if queued is not None:
                queued.append(event)
        raise self._failure(reason="early_exit")


def initialize_codex(pipe):
    rpc = _CodexRpc(pipe)
    rpc.call("initialize", {"clientInfo": {"name": "yanchuan", "version": "0.2.0"}})
    rpc.notify("initialized")
    return rpc


def codex_answer(pipe, settings, prompt, emit):
    rpc = initialize_codex(pipe)
    # Overlay tables are merged by Codex. Disable each configured server explicitly.
    effective = _object(rpc.call("config/read", {"cwd": pipe.directory}).get("config"))
    servers = _object(effective.get("mcp_servers"))
    start = {
        "cwd": pipe.directory,
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "ephemeral": True,
        "modelProvider": "openai",
        "baseInstructions": "You are a meeting assistant. Answer using the supplied text only.",
        "config": {"mcp_servers": {name: {"enabled": False} for name in servers}},
    }
    if model := settings.model.strip():
        start["model"] = model
    thread = _object(rpc.call("thread/start", start).get("thread"))
    if not thread.get("id") or thread.get("ephemeral") is not True:
        raise rpc._failure(reason="thread")

    params = {"threadId": thread["id"], "input": [{"type": "text", "text": prompt}]}
    if settings.reasoning_effort != "default":
        params["effort"] = settings.reasoning_effort
    queued = []
    turn = _object(rpc.call("turn/start", params, queued).get("turn"))
    if not turn.get("id"):
        raise rpc._failure(reason="turn")
    answer, preview_id = None, None
    for event in chain(queued, rpc.notifications()):
        payload = _object(event.get("params"))
        if payload.get("threadId") != thread["id"]:
            continue
        method = event.get("method")
        if method == "turn/completed" and _object(payload.get("turn")).get("id") == turn["id"]:
            completed = payload["turn"]
            if completed.get("status") == "completed":
                return answer
            raise rpc._failure(completed.get("error"), "turn_failed")
        if payload.get("turnId") != turn["id"]:
            continue
        if method in {"item/started", "item/completed"}:
            item = _object(payload.get("item"))
            if item.get("type") != "agentMessage" or item.get("phase") == "commentary":
                continue
            if method == "item/started" and preview_id is None:
                preview_id = item.get("id")
            elif method == "item/completed":
                answer = item.get("text")
        elif (
            method == "item/agentMessage/delta"
            and preview_id
            and preview_id == payload.get("itemId")
        ):
            emit(payload.get("delta"))
    raise rpc._failure(reason="early_exit")
