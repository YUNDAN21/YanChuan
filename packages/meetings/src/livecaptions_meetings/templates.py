"""Named summary templates, independent of UI and configuration storage."""

from dataclasses import dataclass, replace
from uuid import uuid4

from .prompts import SUMMARY_SYSTEM_PROMPT


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    name: str
    prompt: str

    def __post_init__(self):
        if not self.id.strip() or not self.name.strip():
            raise ValueError("模板名称不能为空")
        if not self.prompt.strip():
            raise ValueError("总结提示词不能为空")


def default_prompt_templates() -> list[PromptTemplate]:
    return [
        PromptTemplate("general", "通用整理", SUMMARY_SYSTEM_PROMPT),
        PromptTemplate(
            "meeting",
            "会议纪要",
            SUMMARY_SYSTEM_PROMPT
            + "\n\n本模板侧重会议纪要：按议题整理讨论、已作出的决定和明确的行动项。"
            "行动项仅记录原文确有的负责人和时间；没有决定或待办时省略对应栏目。",
        ),
        PromptTemplate(
            "learning",
            "讲解笔记",
            SUMMARY_SYSTEM_PROMPT
            + "\n\n本模板侧重讲解与学习：围绕核心主题，梳理概念、机制、步骤、推导及适用条件。"
            "突出为什么以及如何做，保留关键数字与例子，使用便于复习的结构。",
        ),
        PromptTemplate(
            "brief",
            "简洁摘要",
            SUMMARY_SYSTEM_PROMPT + "\n\n本模板侧重快速阅读：优先用 3–5 条要点概括核心结论与依据，"
            "尽量控制在 300 个汉字内，保留必要的数字、条件和时间引用，省略次要细节。",
        ),
    ]


class PromptTemplateLibrary:
    def __init__(self, templates: list[PromptTemplate], selected_id: str):
        self._templates = list(templates)
        if not self._templates:
            raise ValueError("至少保留一个提示词模板")
        if len({item.id for item in templates}) != len(templates):
            raise ValueError("提示词模板 ID 不能重复")
        self.select(selected_id)

    @property
    def templates(self) -> list[PromptTemplate]:
        return list(self._templates)

    @property
    def selected(self) -> PromptTemplate:
        return next(item for item in self._templates if item.id == self.selected_id)

    def select(self, template_id: str):
        if not any(item.id == template_id for item in self._templates):
            raise ValueError("提示词模板不存在")
        self.selected_id = template_id

    def add(self, name: str, prompt: str) -> PromptTemplate:
        template = PromptTemplate(uuid4().hex, name.strip(), prompt)
        self._templates.append(template)
        self.select(template.id)
        return template

    def update(self, template_id: str, name: str, prompt: str):
        for index, item in enumerate(self._templates):
            if item.id == template_id:
                self._templates[index] = replace(item, name=name.strip(), prompt=prompt)
                return
        raise ValueError("提示词模板不存在")

    def remove(self, template_id: str):
        if not any(item.id == template_id for item in self._templates):
            raise ValueError("提示词模板不存在")
        if len(self._templates) == 1:
            raise ValueError("至少保留一个提示词模板")
        self._templates = [item for item in self._templates if item.id != template_id]
        if self.selected_id == template_id:
            self.select(self._templates[0].id)
