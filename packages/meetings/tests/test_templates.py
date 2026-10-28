import pytest
from livecaptions_meetings import PromptTemplate, PromptTemplateLibrary, default_prompt_templates


def test_template_edits_selection_and_removal_keep_stable_ids_and_isolated_drafts():
    original = PromptTemplateLibrary(default_prompt_templates(), "general")
    draft = PromptTemplateLibrary(original.templates, original.selected_id)
    template = draft.add(" 项目周会 ", "只整理决定与下一步。")
    draft.update(template.id, "研发周会", "保留负责人及截止日期。")
    draft.select("meeting")
    draft.select(template.id)
    assert draft.selected.name == "研发周会"
    assert draft.selected.prompt == "保留负责人及截止日期。"
    assert len(original.templates) == 4 and original.selected_id == "general"
    draft.remove(template.id)
    assert draft.selected_id == "general"
    assert len(draft.templates) == 4


def test_invalid_template_edits_leave_collection_unchanged_and_last_template_is_kept():
    library = PromptTemplateLibrary([PromptTemplate("a", "唯一模板", "内容")], "a")
    for operation in [
        lambda: library.update("a", "", "内容"),
        lambda: library.update("a", "名称", " \n "),
        lambda: library.add("新模板", ""),
        lambda: library.remove("a"),
        lambda: library.select("missing"),
    ]:
        with pytest.raises(ValueError):
            operation()
        assert library.selected == PromptTemplate("a", "唯一模板", "内容")
        assert len(library.templates) == 1
    with pytest.raises(ValueError, match="重复"):
        PromptTemplateLibrary(library.templates * 2, "a")
