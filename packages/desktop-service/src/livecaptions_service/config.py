import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from livecaptions_meetings import (
    SUMMARY_SYSTEM_PROMPT,
    ApiProfile,
    ApiProfileLibrary,
    PromptTemplate,
    PromptTemplateLibrary,
    default_prompt_templates,
)


@dataclass
class MeetingSettings:
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = ""
    api_key: str = field(default="", repr=False)
    interval_minutes: int = 5
    automatic_summary: bool = True
    summary_prompt: str = SUMMARY_SYSTEM_PROMPT
    prompt_templates: list[PromptTemplate] | None = None
    selected_prompt_template: str = "general"
    protocol: str = "chat_completions"
    api_profiles: list[ApiProfile] | None = None
    selected_api_profile: str = "default"
    chat_api_profile: str | None = None
    chat_context_tokens: int = 272000
    chat_model: str = ""
    chat_reasoning_effort: str = "default"

    def __post_init__(self):
        if self.prompt_templates is None:
            library = PromptTemplateLibrary(default_prompt_templates(), "general")
            if self.summary_prompt != SUMMARY_SYSTEM_PROMPT:
                library.add("当前自定义", self.summary_prompt)
        else:
            library = PromptTemplateLibrary(self.prompt_templates, self.selected_prompt_template)
        self.prompt_templates = library.templates
        self.selected_prompt_template = library.selected_id
        self.summary_prompt = library.selected.prompt
        if self.api_profiles is None:
            self.api_profiles = [
                ApiProfile(
                    "default", "默认 API", self.base_url, self.model, self.api_key, self.protocol
                )
            ]
        selected = self.profile_library().selected
        self.base_url, self.model, self.api_key, self.protocol = (
            selected.base_url,
            selected.model,
            selected.api_key,
            selected.protocol,
        )

    def template_library(self):
        return PromptTemplateLibrary(self.prompt_templates, self.selected_prompt_template)

    def profile_library(self):
        return ApiProfileLibrary(self.api_profiles, self.selected_api_profile)

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("prompt_templates") is not None:
            data["prompt_templates"] = [PromptTemplate(**item) for item in data["prompt_templates"]]
        if data.get("api_profiles") is not None:
            data["api_profiles"] = [ApiProfile(**item) for item in data["api_profiles"]]
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})

    def save(self, path: Path):
        self.profile_library()
        self.template_library()
        if not isinstance(self.chat_model, str) or len(self.chat_model) > 1024:
            raise ValueError("问答模型名称无效")
        if self.chat_reasoning_effort not in {
            "default",
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("不支持的思考强度")
        if (
            isinstance(self.chat_context_tokens, bool)
            or not isinstance(self.chat_context_tokens, int)
            or self.chat_context_tokens < 8192
        ):
            raise ValueError("聊天上下文容量须为至少 8192 的整数 token 数")
        if not 1 <= self.interval_minutes <= 120:
            raise ValueError("自动总结间隔须为 1–120 分钟")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def summary_settings(self):
        return self.profile_library().selected.summary_settings(self.summary_prompt)
