from livecaptions_service.presentation import CaptionHistory, compact_lines, semantic_lines

from livecaptions_asr.results import Segment, Sentence, StreamEvent


def test_revisions_replace_then_sentence_retires_segments_without_duplicates():
    history = CaptionHistory()
    for revision, text in [(1, "今天"), (2, "今天讨论字幕")]:
        history.apply(
            StreamEvent("partial", segment=Segment(0, 0, 2, text, final=False, revision=revision))
        )
    history.apply(StreamEvent("partial", segment=Segment(0, 0, 1, "旧版本", revision=1)))
    assert history.pending.text == "今天讨论字幕"
    assert not history.confirmed
    event = StreamEvent(
        "sentence", sentence=Sentence(0, 0, 2, "今天讨论字幕。", (0,), "semantic_endpoint")
    )
    history.apply(event)
    history.apply(event)
    history.apply(StreamEvent("partial", segment=Segment(0, 0, 2, "迟到版本", revision=3)))
    assert history.pending is None
    assert [entry.text for entry in history.confirmed] == ["今天讨论字幕。"]


def test_duration_fragments_join_until_the_semantic_sentence_arrives():
    history = CaptionHistory()
    history.apply(
        StreamEvent("final", segment=Segment(0, 0, 16, "We should", boundary="max_duration"))
    )
    history.apply(
        StreamEvent("partial", segment=Segment(1, 16, 18, "keep this together", final=False))
    )
    assert history.pending.text == "We should keep this together"
    history.apply(
        StreamEvent(
            "sentence",
            sentence=Sentence(
                0, 0, 18, "We should keep this together.", (0, 1), "semantic_endpoint"
            ),
        )
    )
    assert history.pending is None
    assert len(history.confirmed) == 1


def test_restart_retains_history_without_colliding_ids():
    history = CaptionHistory()
    event = StreamEvent("sentence", sentence=Sentence(0, 0, 2, "こんにちは。", (0,), "end"))
    history.apply(event)
    history.begin_session()
    history.apply(event)
    assert len(history.confirmed) == 2
    assert history.confirmed[0].key != history.confirmed[1].key


def test_two_rows_follow_latest_text_without_removing_full_history():
    history = CaptionHistory()
    history.apply(
        StreamEvent(
            "sentence",
            sentence=Sentence(0, 0, 5, "这是完整的一段字幕。之后继续讨论下一项。", (0,), "end"),
        )
    )
    rows = compact_lines(history, 10, len)
    assert len(rows) == 2
    assert "下一项" in "".join(rows)
    assert history.confirmed[0].text.startswith("这是完整")


def test_wrap_prefers_clause_boundaries_and_preserves_english_words():
    assert semantic_lines("今天确认字幕，明天讨论翻译。", 10, len)[0] == "今天确认字幕，"
    rows = semantic_lines("Live captions stay on top while you work.", 20, len)
    assert all(len(row) <= 20 for row in rows)
    assert " ".join(rows) == "Live captions stay on top while you work."
    assert all(
        not row.startswith(tuple("，。！？、）」"))
        for row in semantic_lines("字幕测试中，下一行。", 6, len)
    )


def test_wrap_preserves_long_identifiers_and_explicit_semantic_breaks():
    assert "".join(semantic_lines("abcdefghijklmnop", 5, len)) == "abcdefghijklmnop"
    assert semantic_lines("第一句。\n第二句。", 40, len) == ["第一句。", "第二句。"]
