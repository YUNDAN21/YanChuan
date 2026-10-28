"""Named API profiles; credentials are excluded from representations."""

from dataclasses import dataclass, field, replace
from uuid import uuid4

from .client import SummarySettings
from .prompts import SUMMARY_SYSTEM_PROMPT
from .protocols import PROTOCOL_LABELS, validate_api_url


@dataclass(frozen=True)
class ApiProfile:
    id: str
    name: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    protocol: str = "chat_completions"

    def __post_init__(self):
        if not self.id.strip() or not self.name.strip():
            raise ValueError("API 配置名称不能为空")
        validate_api_url(self.base_url)
        if self.protocol not in PROTOCOL_LABELS:
            raise ValueError("不支持的总结 API 协议")

    def summary_settings(self, system_prompt=SUMMARY_SYSTEM_PROMPT):
        return SummarySettings(
            self.base_url,
            self.model,
            self.api_key,
            system_prompt=system_prompt,
            protocol=self.protocol,
        )


class ApiProfileLibrary:
    def __init__(self, profiles: list[ApiProfile], selected_id: str):
        self._profiles = list(profiles)
        if not profiles:
            raise ValueError("至少保留一个 API 配置")
        if len({profile.id for profile in profiles}) != len(profiles):
            raise ValueError("API 配置 ID 不能重复")
        self.select(selected_id)

    @property
    def profiles(self):
        return list(self._profiles)

    @property
    def selected(self):
        return next(profile for profile in self._profiles if profile.id == self.selected_id)

    def select(self, profile_id):
        if not any(profile.id == profile_id for profile in self._profiles):
            raise ValueError("API 配置不存在")
        self.selected_id = profile_id

    def add(self, name, base_url, model, api_key, protocol="chat_completions"):
        profile = ApiProfile(
            uuid4().hex, name.strip(), base_url.strip(), model.strip(), api_key.strip(), protocol
        )
        self._profiles.append(profile)
        self.select(profile.id)
        return profile

    def update(self, profile_id, **changes):
        for index, profile in enumerate(self._profiles):
            if profile.id == profile_id:
                if "id" in changes:
                    raise ValueError("API 配置 ID 不可更改")
                self._profiles[index] = replace(profile, **changes)
                return
        raise ValueError("API 配置不存在")

    def remove(self, profile_id):
        if not any(profile.id == profile_id for profile in self._profiles):
            raise ValueError("API 配置不存在")
        if len(self._profiles) == 1:
            raise ValueError("至少保留一个 API 配置")
        self._profiles = [profile for profile in self._profiles if profile.id != profile_id]
        if self.selected_id == profile_id:
            self.select(self._profiles[0].id)
