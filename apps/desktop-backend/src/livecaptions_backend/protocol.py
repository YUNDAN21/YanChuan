"""Version 1 request routing and JSON conversion, without GUI dependencies."""

from dataclasses import asdict, is_dataclass
from pathlib import Path

from livecaptions_service import InputSelection
from livecaptions_service.presentation import semantic_lines


def json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


class Router:
    def __init__(self, service):
        self.service = service

    def dispatch(self, method, params):
        s = self.service
        if method == "hello":
            return {"protocol": 1, "configuration": s.configuration(), "snapshot": s.snapshot()}
        if method == "snapshot":
            return s.snapshot()
        if method == "devices.list":
            return s.devices()
        if method == "session.start":
            return s.start(
                params.get("mode", "meeting"),
                title=params.get("title", ""),
                selection=InputSelection(**params.get("selection", {})),
                file=params.get("file"),
            )
        if method == "session.stop":
            s.stop(cancel=params.get("cancel", False))
        elif method == "meetings.list":
            return s.list_meetings()
        elif method == "meetings.open":
            return s.open_meeting(params["directory"])
        elif method == "meetings.rename":
            return s.rename_meeting(params["title"])
        elif method == "meetings.delete":
            return s.delete_meeting(params["directory"])
        elif method == "meetings.content":
            return s.meeting_content(params.get("kind", "all"))
        elif method == "chat.list":
            return s.chat_messages()
        elif method == "chat.usage":
            return s.chat_usage(params.get("question", ""))
        elif method == "chat.send":
            return s.send_chat(params["question"])
        elif method == "chat.cancel":
            return s.cancel_chat(params.get("request_id"))
        elif method == "chat.configure":
            s.configure_chat(
                params.get("api_profile_id"),
                params.get("context_tokens"),
                model=params.get("model"),
                reasoning_effort=params.get("reasoning_effort"),
            )
        elif method == "summary.request":
            return s.summarize_now()
        elif method == "schedule.set":
            s.configure_schedule(params["minutes"], params["automatic"])
        elif method == "configuration.get":
            return s.configuration()
        elif method == "models.list":
            return s.model_inventory()
        elif method == "models.configure":
            return s.configure_models(
                params["selected_model"],
                params.get("source", "huggingface"),
                params.get("directory"),
                params.get("model_file"),
            )
        elif method == "models.download":
            return s.download_models()
        elif method == "models.cancel":
            s.models.cancel()
        elif method == "profiles.save":
            s.set_api_profiles(params["profiles"], params["selected_id"])
        elif method == "profiles.select":
            s.select_api_profile(params["id"])
        elif method == "profiles.models":
            return s.api_models(
                params["base_url"],
                params.get("api_key", ""),
                params.get("protocol", "chat_completions"),
            )
        elif method == "templates.save":
            s.set_templates(params["templates"], params["selected_id"])
        elif method == "templates.defaults":
            return s.default_templates()
        elif method == "appearance.set":
            s.set_appearance(params["mode"])
        elif method == "captions.configure":
            s.configure_captions(params)
        elif method == "subtitle.split":
            # Frontends supply actual glyph widths; semantic rules stay in the core library.
            widths = params.get("glyph_widths", {})
            return semantic_lines(
                params["text"], params["width"], lambda text: sum(widths.get(c, 12) for c in text)
            )
        elif method == "shutdown":
            s.close()
            return {"closed": True}
        else:
            raise ValueError("不支持的接口方法")
        return True
