"""Text generation through a user-installed Codex or Antigravity CLI."""

import json
import logging
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .agent_process import AgentProcessError, agent_process
from .agent_wire import CODEX_APP_SERVER, agy_answer, codex_answer
from .errors import SummaryError
from .prompts import SUMMARY_SYSTEM_PROMPT, summary_input

logger = logging.getLogger(__name__)

AGENT_LABELS = {"codex": "Codex", "agy": "Antigravity (agy)"}
AGENT_EFFORTS = {
    "codex": {"default", "none", "low", "medium", "high", "xhigh", "max"},
    "agy": {"default", "low", "medium", "high", "xhigh", "max"},
}


@dataclass(frozen=True)
class LocalAgentSettings:
    agent: str
    executable: str = ""
    model: str = ""
    system_prompt: str = SUMMARY_SYSTEM_PROMPT
    reasoning_effort: str = "default"
    timeout_seconds: float = 90

    def __post_init__(self):
        if self.agent not in AGENT_LABELS:
            raise ValueError("不支持的本地 Agent")
        if self.reasoning_effort not in AGENT_EFFORTS[self.agent]:
            raise ValueError(f"{AGENT_LABELS[self.agent]} 不支持所选思考强度")
        if not all(isinstance(value, str) for value in (self.executable, self.model)):
            raise ValueError("本地 Agent 路径或模型无效")
        if self.model.strip().startswith("-") or "\0" in self.model or "\0" in self.executable:
            raise ValueError("本地 Agent 路径或模型无效")
        if (
            not self.system_prompt.strip()
            or not math.isfinite(self.timeout_seconds)
            or (self.timeout_seconds <= 0)
        ):
            raise ValueError("本地 Agent 需要有效的提示词和超时时间")


def _find_executable(settings):
    name = settings.agent + (".exe" if os.name == "nt" else "")
    requested = settings.executable.strip()
    if requested:
        path = Path(requested).expanduser()
        if (
            path.is_absolute()
            and path.is_file()
            and (os.name != "nt" or path.suffix.lower() == ".exe")
        ):
            return str(path)
        raise SummaryError(f"请选择有效的 {name} 完整路径")
    if found := shutil.which(name):
        path = Path(found)
        if path.is_absolute():
            return found
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if settings.agent == "agy" and local:
            path = Path(local) / "agy/bin/agy.exe"
            if path.is_file():
                return str(path)
        roaming = os.environ.get("APPDATA")
        if settings.agent == "codex" and roaming:
            npm = Path(roaming) / "npm/node_modules/@openai/codex"
            dist = "vendor/x86_64-pc-windows-msvc/bin/codex.exe"
            for path in (npm / dist, npm / "node_modules/@openai/codex-win32-x64" / dist):
                if path.is_file():
                    return str(path)
    raise SummaryError(f"未找到 {name}，请在 AI 配置中填写命令行程序路径")


def local_agent_version(settings: LocalAgentSettings) -> str:
    label = AGENT_LABELS[settings.agent]
    with agent_process([_find_executable(settings), "--version"], label, timeout=10) as pipe:
        pipe.close_input()
        version = None
        for line in pipe.lines():
            if version is None:
                version = re.search(rb"\b\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?\b", line)
        if pipe.wait() != 0 or version is None:
            raise SummaryError(f"无法读取 {label} 版本，请检查所选命令行程序")
        return version.group().decode("ascii")


def _agy_options(settings):
    denied = ["read_file", "write_file", "read_url", "execute_url", "command", "mcp"]
    overrides = {
        "agentMode": "",
        "toolPermission": "strict",
        "permissions": {"deny": [f"{tool}(*)" for tool in denied]},
    }
    command = [
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--disable-slash-commands",
        "--settings",
        json.dumps(overrides),
        "--print-timeout",
        f"{math.ceil(settings.timeout_seconds)}s",
    ]
    if model := settings.model.strip():
        command.append(f"--model={model}")
    if settings.reasoning_effort != "default":
        command.append(f"--effort={settings.reasoning_effort}")
    return command


class LocalAgentClient:
    def __init__(self, settings: LocalAgentSettings):
        self.settings = settings

    @property
    def model(self):
        return self.settings.model.strip() or AGENT_LABELS[self.settings.agent]

    def summarize(self, title, entries, previous_summary=""):
        return self.generate(summary_input(title, entries, previous_summary))

    def generate(self, content):
        return self.chat([{"role": "user", "content": content}])

    def chat(self, messages):
        return self.chat_cancellable(messages, None)

    def chat_cancellable(self, messages, cancelled):
        return self.stream_chat(messages, cancelled)

    def stream_chat(self, messages, cancelled, *, on_delta=None, request_id=""):
        request_id = request_id or uuid4().hex
        settings = self.settings
        label = AGENT_LABELS[settings.agent]
        prompt = (
            f"{settings.system_prompt}\n\n"
            "以下是言川提供的 JSON 对话，最后一条 user 消息为本次输入。直接返回任务要求的结果。\n"
            + json.dumps(messages, ensure_ascii=False)
        )
        arguments = CODEX_APP_SERVER if settings.agent == "codex" else _agy_options(settings)
        started = monotonic()
        first_delta = None
        deltas = 0
        text_length = 0
        pipe = None
        reason = "completed"

        def emit(delta):
            nonlocal first_delta, deltas
            if not isinstance(delta, str) or not delta:
                return
            deltas += 1
            if first_delta is None:
                first_delta = monotonic() - started
                logger.info(
                    "agent first text provider=%s request=%s elapsed=%.3fs",
                    settings.agent,
                    request_id,
                    first_delta,
                )
            if on_delta is not None:
                on_delta(delta)

        logger.info(
            "agent started provider=%s request=%s timeout=%.1fs effort=%s input_chars=%d",
            settings.agent,
            request_id,
            settings.timeout_seconds,
            settings.reasoning_effort,
            len(prompt),
        )
        try:
            with agent_process(
                [_find_executable(settings), *arguments],
                label,
                timeout=settings.timeout_seconds,
                cancelled=cancelled,
            ) as pipe:
                if settings.agent == "codex":
                    answer = codex_answer(pipe, settings, prompt, emit)
                else:
                    answer = agy_answer(pipe, prompt, emit)
                if not isinstance(answer, str) or not answer.strip():
                    raise AgentProcessError("empty", f"{label} 未返回有效正文，请重试")
                text_length = len(answer.strip())
                return answer.strip()
        except InterruptedError:
            reason = "cancelled"
            raise
        except SummaryError as exc:
            reason = getattr(exc, "reason", "executable")
            raise
        except Exception as exc:  # noqa: BLE001 - keep CLI payloads and traces out of summaries
            reason = "internal"
            logger.warning("agent handler failed type=%s", type(exc).__name__)
            raise SummaryError(f"{label} 请求处理失败，请查看诊断日志") from None
        finally:
            logger.log(
                logging.INFO if reason in {"completed", "cancelled"} else logging.WARNING,
                "agent ended provider=%s request=%s result=%s elapsed=%.3fs "
                "first_text=%s deltas=%d output_chars=%d stdout_lines=%d stderr_bytes=%d exit_code=%s",
                settings.agent,
                request_id,
                reason,
                monotonic() - started,
                f"{first_delta:.3f}s" if first_delta is not None else None,
                deltas,
                text_length,
                pipe.stdout_lines if pipe else 0,
                pipe.stderr_bytes if pipe else 0,
                pipe.process.returncode if pipe else None,
            )
