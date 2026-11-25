"""Discover a local Agent's executable and model catalogue without submitting a turn."""

import json
import logging
from dataclasses import dataclass
from time import monotonic

from .agent_process import AgentProcessError, agent_process
from .agent_wire import CODEX_APP_SERVER, agent_failure, initialize_codex
from .errors import SummaryError
from .local_agent import AGENT_LABELS, LocalAgentSettings, _find_executable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentModel:
    id: str
    label: str


@dataclass(frozen=True)
class AgentCatalog:
    executable: str
    default_model: str
    models: list[AgentModel]
    warning: str = ""


def _model_id(value):
    if isinstance(value, str) and len(value) <= 1024 and value.isprintable():
        return value.strip()
    return ""


def _codex_catalog(executable, label, include_models):
    default_model = ""
    models = {}
    try:
        with agent_process([executable, *CODEX_APP_SERVER], label, timeout=15) as pipe:
            rpc = initialize_codex(pipe)
            config = rpc.call("config/read", {"cwd": pipe.directory}).get("config")
            if isinstance(config, dict):
                default_model = _model_id(config.get("model"))
            if not include_models and default_model:
                return AgentCatalog(executable, default_model, [])

            params = {"limit": 100, "includeHidden": False, "modelProvider": "openai"}
            cursors = set()
            while True:
                page = rpc.call("model/list", params)
                items = page.get("data")
                if not isinstance(items, list):
                    raise SummaryError("Codex 返回的模型列表格式无效")
                for item in items:
                    if not isinstance(item, dict) or item.get("hidden") is True:
                        continue
                    identifier = _model_id(item.get("model")) or _model_id(item.get("id"))
                    if not identifier:
                        continue
                    if not default_model and item.get("isDefault") is True:
                        default_model = identifier
                    if include_models:
                        models[identifier] = AgentModel(
                            identifier, _model_id(item.get("displayName")) or identifier
                        )
                cursor = page.get("nextCursor")
                if cursor is None or (default_model and not include_models):
                    break
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise SummaryError("Codex 返回的模型分页信息无效")
                cursors.add(cursor)
                params["cursor"] = cursor
    except SummaryError as exc:
        return AgentCatalog(executable, default_model, list(models.values()), str(exc))
    return AgentCatalog(executable, default_model, list(models.values()))


def _agy_output(executable, label, arguments):
    with agent_process([executable, *arguments], label, timeout=18) as pipe:
        pipe.close_input()
        output = b"".join(pipe.lines())
        if pipe.wait() != 0:
            diagnostic = (output + pipe.stderr_tail).decode("utf-8", errors="replace")
            raise agent_failure(label, diagnostic, "catalog")
    return output.decode("utf-8-sig", errors="replace")


def _agy_default_model(executable, label):
    output = _agy_output(executable, label, ["--print", "/model", "--output-format", "json"])
    try:
        response = json.loads(output)
        command = response["command"]
        if (
            response["status"] == "SUCCESS"
            and command["name"] == "model"
            and (identifier := _model_id(command["data"]["id"]))
        ):
            return identifier
    except (ValueError, TypeError, KeyError):
        pass
    raise AgentProcessError("catalog_default", "agy 未返回当前模型")


def _agy_catalog(executable, label, include_models):
    default_model, models, warnings = "", {}, []
    try:
        default_model = _agy_default_model(executable, label)
    except SummaryError as exc:
        warnings.append(str(exc))
    if include_models:
        try:
            for line in _agy_output(executable, label, ["models"]).splitlines():
                identifier, separator, title = line.partition("\t")
                if (
                    separator
                    and (identifier := _model_id(identifier))
                    and not any(character.isspace() for character in identifier)
                ):
                    models[identifier] = AgentModel(identifier, _model_id(title) or identifier)
        except SummaryError as exc:
            warnings.append(str(exc))
    return AgentCatalog(
        executable, default_model, list(models.values()), " ".join(dict.fromkeys(warnings))
    )


def local_agent_catalog(settings: LocalAgentSettings, *, include_models=True) -> AgentCatalog:
    started = monotonic()
    executable = _find_executable(settings)
    label = AGENT_LABELS[settings.agent]
    query = _codex_catalog if settings.agent == "codex" else _agy_catalog
    catalog = query(executable, label, include_models)
    logger.info(
        "agent catalog provider=%s default_model_known=%s models=%d warning=%s elapsed=%.3fs",
        settings.agent,
        bool(catalog.default_model),
        len(catalog.models),
        bool(catalog.warning),
        monotonic() - started,
    )
    return catalog
