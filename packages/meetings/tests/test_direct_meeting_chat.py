import json
import threading

import pytest
from livecaptions_meetings import MeetingAgent, MeetingStore, SummarySettings, create_summary_client
from livecaptions_meetings.answer_preview import answer_preview
from livecaptions_meetings.chat import build_chat_messages
from livecaptions_meetings.meeting_agent import AGENT_PROMPT, AgentError
from test_meeting_agent import Scripted, reference, run
from test_protocols import completed, server_response


def test_short_meeting_uses_one_request_with_entire_source_and_history():
    source = reference(9)
    client = Scripted([{"text": "确定周五发布。", "citations": [0, 8]}])
    agent = MeetingAgent(source, client)
    messages = [
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的答复"},
        {"role": "user", "content": "整场会议的决定"},
    ]
    text = agent.answer(messages, threading.Event(), lambda _: None)
    assert len(client.calls) == 1
    turns, task = client.calls[0]
    assert task["phase"] == "direct"
    original = json.loads(
        turns[0]["content"].split("会议完整原文（JSON）：\n")[1].split("\n\n聊天与问题：\n")[0]
    )
    assert [item["text"] for item in original["source"]] == [c.text for c in source.chunks]
    assert turns[0]["content"].endswith(messages[0]["content"])
    assert turns[1] == messages[1]
    assert "[00:08:00–00:08:20]" in text
    usage = agent.input_usage(messages)
    assert not usage["retrieval_mode"] and usage["tool_tokens"] == 0
    assert usage["estimated_tokens"] + 4096 <= agent.context_tokens


@pytest.mark.parametrize("protocol", ["responses", "chat_completions", "anthropic"])
def test_direct_meeting_http_roundtrip_has_one_request(protocol):
    with server_response(completed(protocol, '{"text":"周五验收。","citations":[0]}')) as (
        base,
        received,
    ):
        client = create_summary_client(
            SummarySettings(
                base, "fixture", "fixture-key", protocol=protocol, system_prompt=AGENT_PROMPT
            )
        )
        text, _ = run(MeetingAgent(reference(), client))
        assert text.startswith("周五验收") and len(received) == 1
        body = received[0][2]
        turns = body["input"] if protocol == "responses" else body["messages"]
        user_turn = next(turn for turn in turns if turn["role"] == "user")
        task = json.loads(turns[-1]["content"].split("查阅任务（JSON）：\n")[-1])
        original = json.loads(
            user_turn["content"].split("会议完整原文（JSON）：\n")[1].split("\n\n聊天与问题：\n")[0]
        )
        assert task["phase"] == "direct" and len(original["source"]) == 3


def test_direct_selection_counts_history_and_preserves_long_meeting_retrieval():
    source = reference(8)
    agent = MeetingAgent(source, None, context_tokens=8192)
    assert agent.can_answer_directly([{"role": "user", "content": "问题"}])
    assert not agent.can_answer_directly([{"role": "user", "content": "历史" * 5000}])
    limited = MeetingAgent(source, None, full_context=False)
    assert not limited.can_answer_directly([{"role": "user", "content": "问题"}])


@pytest.mark.parametrize("capacity", [256000, 1000000])
def test_full_context_has_no_short_meeting_threshold(capacity):
    from livecaptions_meetings import MeetingReference, TranscriptEntry

    source = MeetingReference(
        "m", "长会议", [TranscriptEntry(1, "a", 0, 7200, "完整会议原文。" * 6000)]
    )
    assert source.source_tokens > 32768
    agent = MeetingAgent(source, None, context_tokens=capacity)
    usage = agent.input_usage([{"role": "user", "content": "整场决策"}])
    assert not usage["retrieval_mode"]
    assert usage["estimated_tokens"] + 4096 < capacity


def test_direct_answer_rejects_invented_source():
    with pytest.raises(AgentError, match="来源"):
        run(MeetingAgent(reference(), Scripted([{"text": "编造", "citations": [999]}])))


def test_answer_preview_decodes_only_the_top_level_body():
    prefix = r'{"notes":"暂存资料","text":"公式 $\\alpha$\n\u4e2d'
    assert answer_preview(prefix) == "公式 $\\alpha$\n中"
    assert answer_preview(r'{"text":"周五\uD83D') == "周五"
    assert answer_preview(r'{"text":"周五\uD83D\uDE00') == "周五😀"
    assert answer_preview('{"findings":[{"text":"中间资料"}]}') == ""
    assert answer_preview('{"action":"read","text":"中间资料', require_action=True) == ""
    assert answer_preview('{"text":"校对中","action":"answer"', require_action=True) == "校对中"


def test_direct_reference_uses_submit_time_snapshot(tmp_path):
    store = MeetingStore.create(tmp_path, "快照")
    try:
        store.append("first", 0, 1, "首次确认")
        source = store.reference_snapshot()
        store.append("later", 1, 2, "后来追加")
        client = Scripted([{"text": "首次确认", "citations": [0]}])
        agent = MeetingAgent(source, client)
        agent.answer(
            build_chat_messages(source.overview(), [], "内容"), threading.Event(), lambda _: None
        )
        assert "后来追加" not in str(client.calls)
    finally:
        store.close()
