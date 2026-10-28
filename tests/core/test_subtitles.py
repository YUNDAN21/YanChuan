import math
import subprocess
import sys
from itertools import pairwise

import pytest

from livecaptions_asr import ASRError, SubtitleSplitter


def test_sentence_clause_boundaries_preserve_original_text_and_offsets():
    text = "今天先确认实时字幕的显示效果，再一起讨论下一步的会议记录功能。然后确定上线时间。"
    cues = SubtitleSplitter().split(text)
    assert [cue.text for cue in cues] == [
        "今天先确认实时字幕的显示效果，",
        "再一起讨论下一步的会议记录功能。",
        "然后确定上线时间。",
    ]
    assert "".join(cue.text for cue in cues) == text
    assert all(text[cue.start : cue.end] == cue.text for cue in cues)
    assert cues[0].boundary == "clause"


@pytest.mark.parametrize(
    "text",
    [
        "中文没有标点的长段也需要保留全部文字同时按照实际的显示宽度拆成更容易阅读的字幕",
        "字幕を短く整理します。そして、前の文章を読みやすく表示します。",
        "We should keep the original words, because subtitles need readable phrases.",
        "  Dr. Smith measured 3.14 seconds. Next, we checked the result.\n",
        "abcdefghijklmnopqrstuvwxyz0123456789",
    ],
)
def test_width_limit_and_lossless_reconstruction_for_cjk_and_english(text):
    cues = SubtitleSplitter().split(text, max_width=15, measure=len)
    assert "".join(cue.text for cue in cues) == text
    assert all(len(cue.text.strip()) <= 15 for cue in cues)
    assert all(a.end == b.start for a, b in pairwise(cues))


def test_phrase_boundary_keeps_english_words_and_cjk_connectors():
    english = SubtitleSplitter(32).split(
        "We prepare the subtitles because the previous sentence is too long"
    )
    assert english[0].text.strip() == "We prepare the subtitles"
    assert english[0].boundary == "phrase"
    chinese = SubtitleSplitter(20).split("今天先把字幕做好然后再一起处理会议记录")
    assert chinese[0].text == "今天先把字幕做好"
    assert chinese[1].text.startswith("然后")


def test_small_sentences_always_stay_separate_and_decimal_is_not_sentence_end():
    assert [cue.text for cue in SubtitleSplitter().split("第一句。第二句。")] == [
        "第一句。",
        "第二句。",
    ]
    assert [cue.text for cue in SubtitleSplitter().split("Dr. Smith took 3.14 seconds. Done.")] == [
        "Dr. Smith took 3.14 seconds. ",
        "Done.",
    ]


def test_splitter_requires_valid_width():
    assert SubtitleSplitter().split("  ") == ()
    for width in [0, -1, math.inf, math.nan]:
        with pytest.raises(ASRError):
            SubtitleSplitter().split("hello", max_width=width, measure=len)
    with pytest.raises(ASRError):
        SubtitleSplitter().split("hello", max_width=10)
    with pytest.raises(ASRError):
        SubtitleSplitter(0)
    with pytest.raises(ASRError, match="一个字符"):
        SubtitleSplitter().split("字", max_width=0.5, measure=len)


def test_splitter_imports_and_runs_without_models_or_applications():
    code = """
import sys
class Blocked:
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in {
            "torch", "transformers", "onnxruntime", "PySide6", "fastapi",
            "livecaptions_testapp", "livecaptions_audio", "livecaptions_service", "livecaptions_backend",
        }:
            raise AssertionError("unexpected import: " + fullname)
sys.meta_path.insert(0, Blocked())
from livecaptions_asr import SubtitleSplitter
assert len(SubtitleSplitter().split("First sentence. Second sentence.")) == 2
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
