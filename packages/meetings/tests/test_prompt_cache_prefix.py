import json
import os
import threading

from livecaptions_meetings import ChatMessage, MeetingAgent, MeetingReference, TranscriptEntry
from livecaptions_meetings.chat import build_chat_messages
from livecaptions_meetings.client import response_diagnostic
from test_meeting_agent import Scripted


def capture(source, question, history=(), memory=None):
    client = Scripted([{"text": "确认。", "citations": [0]}])
    agent = MeetingAgent(source, client)
    original = build_chat_messages(source.overview(), history, question, memory=memory)
    agent.answer(original, threading.Event(), lambda _: None)
    return client.calls[0][0]


def test_questions_and_compression_memory_follow_the_entire_stable_source():
    source = MeetingReference("m", "合成会议", [TranscriptEntry(1, "a", 0, 20, "原文内容。" * 500)])
    first = capture(source, "何时发布？")
    other = capture(source, "谁负责？", memory={"summary": "旧聊天摘要"})
    prefix = first[0]["content"].split("\n\n聊天与问题：\n")[0]
    assert other[0]["content"].startswith(prefix)
    assert prefix.index("原文内容") < first[0]["content"].index("何时发布")
    assert "旧聊天摘要" not in prefix and "旧聊天摘要" in other[0]["content"]
    assert first[0]["content"].count("原文内容。") == 500
    assert len(first) == len(other) == 1


def test_growing_meeting_keeps_old_source_before_changed_overview_and_chat():
    entries = [
        TranscriptEntry(1, "a", 0, 20, "原始内容。" * 160),
        TranscriptEntry(2, "b", 20, 30, "第二段"),
    ]
    first = MeetingReference("m", "会议", entries)
    grown = MeetingReference("m", "会议", entries + [TranscriptEntry(3, "c", 30, 40, "新追加内容")])
    history = [
        ChatMessage(1, "r", "user", "旧问题", "completed", "", "model", "", 2),
        ChatMessage(2, "r", "assistant", "旧回答", "completed", "", "model", "", 2),
    ]
    before = capture(first, "问题甲")[0]["content"]
    after = capture(grown, "问题乙", history)
    common = os.path.commonprefix([before, after[0]["content"]])
    assert "第二段" in common and "新追加内容" not in common
    assert after[1] == {"role": "assistant", "content": "旧回答"}
    assert after[-1]["content"].startswith("问题乙")
    payload = json.loads(after[-1]["content"].split("查阅任务（JSON）：\n")[-1])
    assert payload == {"phase": "direct"}


def test_cache_usage_adapters_do_not_treat_missing_statistics_as_a_cache_miss():
    for usage in [
        {"input_tokens": 9000, "input_tokens_details": {"cached_tokens": 4864}},
        {"prompt_tokens": 9000, "prompt_tokens_details": {"cached_tokens": 4864}},
        {"prompt_tokens": 9000, "prompt_cache_hit_tokens": 4864},
        {"input_tokens": 9000, "cache_read_input_tokens": 4864},
    ]:
        counters = response_diagnostic({"usage": usage})
        assert counters["input_tokens"] == 9000 and counters["cached_tokens"] == 4864
    assert response_diagnostic({})["cached_tokens"] is None
    assert (
        response_diagnostic({"usage": {"input_tokens_details": {"cached_tokens": 0}}})[
            "cached_tokens"
        ]
        == 0
    )
