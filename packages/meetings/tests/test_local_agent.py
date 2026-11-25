import json
import sys
import threading
from contextlib import contextmanager

import pytest
from livecaptions_meetings import (
    ApiProfile,
    LocalAgentSettings,
    SummaryError,
    TranscriptEntry,
    agent_catalog,
    create_summary_client,
    local_agent,
    local_agent_catalog,
)
from livecaptions_meetings.agent_process import agent_process


class ScriptedPipe:
    label = "fixture"
    directory = "isolated"
    stdout_lines = 0
    stderr_bytes = 0

    def __init__(self, events, *, error=b"", code=0):
        self.events, self.stderr_tail, self.code = events, error, code
        self.sent = []
        self.process = self
        self.returncode = code

    def send(self, data):
        self.sent.append(json.loads(data))

    def lines(self):
        for event in self.events():
            self.stdout_lines += 1
            yield json.dumps(event).encode()

    def close_input(self):
        pass

    def wait(self):
        return self.code


def test_codex_catalog_paginates_and_reports_configured_model(monkeypatch):
    pages = [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"config": {"model": "configured"}}},
        {
            "id": 3,
            "result": {"data": [{"id": "recommended", "isDefault": True}], "nextCursor": "next"},
        },
        {"id": 4, "result": {"data": [{"id": "configured", "displayName": "Configured model"}]}},
    ]
    pipe = ScriptedPipe(lambda: iter(pages))

    @contextmanager
    def open_pipe(*args, **kwargs):
        yield pipe

    monkeypatch.setattr(agent_catalog, "_find_executable", lambda _: "fixture.exe")
    monkeypatch.setattr(agent_catalog, "agent_process", open_pipe)
    catalog = local_agent_catalog(LocalAgentSettings("codex"))
    assert catalog.executable == "fixture.exe" and catalog.default_model == "configured"
    assert [(model.id, model.label) for model in catalog.models] == [
        ("recommended", "recommended"),
        ("configured", "Configured model"),
    ]
    assert [message["method"] for message in pipe.sent] == [
        "initialize",
        "initialized",
        "config/read",
        "model/list",
        "model/list",
    ]
    assert pipe.sent[-1]["params"]["cursor"] == "next"


def test_agy_catalog_reads_current_selection_and_tab_separated_model_ids(monkeypatch):
    commands = []

    def query(executable, label, arguments):
        commands.append(arguments)
        if arguments == ["models"]:
            return "my-current\tCurrent model\nanother\tAnother model\n"
        return json.dumps(
            {
                "status": "SUCCESS",
                "command": {
                    "name": "model",
                    "data": {"id": "my-current"},
                },
            }
        )

    monkeypatch.setattr(agent_catalog, "_find_executable", lambda _: "fixture.exe")
    monkeypatch.setattr(agent_catalog, "_agy_output", query)
    settings = LocalAgentSettings("agy")
    catalog = local_agent_catalog(settings, include_models=False)
    assert catalog.default_model == "my-current" and catalog.models == []
    assert commands == [["--print", "/model", "--output-format", "json"]]
    catalog = local_agent_catalog(settings)
    assert [(model.id, model.label) for model in catalog.models] == [
        ("my-current", "Current model"),
        ("another", "Another model"),
    ]


@pytest.mark.parametrize("agent", ["codex", "agy"])
def test_agent_client_streams_body_and_keeps_final_result(monkeypatch, agent, caplog):
    caplog.set_level("INFO", logger=local_agent.__name__)
    calls = []
    answer = "合成结果"
    previews = []

    def rpc(method, **params):
        return {"method": method, "params": {"threadId": "t", "turnId": "u", **params}}

    def events():
        if agent == "agy":
            yield {
                "event": "step_update",
                "step_update": {"step_type": "tool", "text_delta": "hidden"},
            }
            yield {
                "event": "step_update",
                "step_update": {"step_type": "agent_response", "text_delta": "合成"},
            }
            if len(calls) == 2:
                assert previews == ["合成"]
            yield {"event": "result", "result": {"status": "SUCCESS", "response": answer}}
            return
        yield {"id": 1, "result": {}}
        yield {"id": 2, "result": {"config": {"mcp_servers": {"external": {"enabled": True}}}}}
        yield {"id": 3, "result": {"thread": {"id": "t", "ephemeral": True}}}
        yield rpc("item/agentMessage/delta", itemId="commentary", delta="hidden")
        item = {"type": "agentMessage", "id": "a", "phase": "final_answer"}
        yield rpc("item/started", item=item)
        yield {"id": 4, "result": {"turn": {"id": "u"}}}
        yield rpc("item/agentMessage/delta", itemId="a", delta="合成")
        if len(calls) == 2:
            assert previews == ["合成"]
        yield rpc("item/completed", item={**item, "text": answer})
        yield rpc("turn/completed", turn={"id": "u", "status": "completed"})

    @contextmanager
    def open_pipe(command, label, *, timeout, cancelled):
        pipe = ScriptedPipe(events, error=b"private fixture diagnostics")
        calls.append((command, pipe, cancelled))
        assert label == local_agent.AGENT_LABELS[agent]
        assert timeout == 90
        yield pipe

    monkeypatch.setattr(local_agent, "agent_process", open_pipe)
    monkeypatch.setattr(local_agent, "_find_executable", lambda _: "fixture.exe")
    profile = ApiProfile("local", "本机", model="fixture-model", connection=agent)
    client = create_summary_client(profile.summary_settings("使用中文。"))
    entry = TranscriptEntry(1, "001", 30, 34, "周五交付。")
    assert client.summarize("合成会议", [entry], "上一段") == answer
    messages = [{"role": "assistant", "content": "上一问"}, {"role": "user", "content": "再问"}]
    cancel = threading.Event()
    assert client.stream_chat(messages, cancel, on_delta=previews.append, request_id="r1") == answer

    def prompt(pipe):
        message = pipe.sent[-1]
        return (
            message["params"]["input"][0]["text"]
            if agent == "codex"
            else message["message"]["content"]
        )

    command, pipe, _ = calls[0]
    summary = json.loads(prompt(pipe).splitlines()[-1])[0]["content"]
    assert json.loads(summary)["new_transcript"] == [entry.markdown()]
    assert json.loads(summary)["previous_context"] == "上一段"
    assert not any("周五交付" in argument for argument in command)
    assert json.loads(prompt(calls[1][1]).splitlines()[-1]) == messages and calls[1][2] is cancel
    if agent == "codex":
        assert {"app-server", "shell_tool", "plugins"} <= set(command)
        thread = next(m["params"] for m in pipe.sent if m["method"] == "thread/start")
        assert thread["ephemeral"] and thread["sandbox"] == "read-only"
        assert thread["config"]["mcp_servers"] == {"external": {"enabled": False}}
    else:
        settings = json.loads(command[command.index("--settings") + 1])
        assert {"command(*)", "read_file(*)", "mcp(*)"} <= set(settings["permissions"]["deny"])
    assert f"provider={agent} request=r1 result=completed" in caplog.text
    assert "first_text=" in caplog.text and "deltas=1" in caplog.text
    assert all(secret not in caplog.text for secret in ("周五交付", "合成结果", "private fixture"))


def test_agent_failure_returns_safe_message(monkeypatch):
    @contextmanager
    def open_pipe(*args, **kwargs):
        yield ScriptedPipe(list, error=b"private diagnostic: not logged in", code=1)

    monkeypatch.setattr(local_agent, "_find_executable", lambda _: "fixture.exe")
    monkeypatch.setattr(local_agent, "agent_process", open_pipe)
    client = create_summary_client(LocalAgentSettings("agy"))
    with pytest.raises(SummaryError, match="命令行中登录") as failure:
        client.generate("private input")
    assert "private" not in str(failure.value)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console attachment")
def test_agent_pipes_work_without_an_attached_console():
    probe = (
        "import ctypes, sys\n"
        "assert ctypes.windll.kernel32.GetConsoleCP() == 0\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
        "sys.stderr.write('diagnostic')\n"
    )
    with agent_process([sys._base_executable, "-c", probe], "fixture", timeout=8) as pipe:
        pipe.send(b"agent response")
        pipe.close_input()
        assert list(pipe.lines()) == [b"agent response"]
        assert pipe.wait() == 0
    assert pipe.stderr_tail == b"diagnostic"


def test_cancellation_terminates_cli_with_pending_stdin():
    cancel = threading.Event()
    probe = "import time; print('first', flush=True); time.sleep(20)"
    with (
        pytest.raises(InterruptedError),
        agent_process(
            [sys._base_executable, "-c", probe], "fixture", timeout=8, cancelled=cancel
        ) as pipe,
    ):
        assert pipe.read_line().strip() == b"first" and pipe.process.poll() is None
        pipe.send(b"x" * (512 * 1024))
        cancel.set()
        pipe.read_line()
    assert pipe.process.poll() is not None
