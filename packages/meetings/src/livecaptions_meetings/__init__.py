"""Persistent meeting notes and summaries, usable without a UI or recognition model."""

from .chat import (
    CHAT_SYSTEM_PROMPT,
    ChatClient,
    ChatCoordinator,
    ChatHistory,
    ChatMessage,
    chat_context_usage,
)
from .client import (
    ChatSummaryClient,
    HttpSummaryClient,
    SummaryError,
    SummarySettings,
    create_summary_client,
)
from .deletion import delete_meeting, meeting_for_deletion
from .model_catalog import ModelCatalogError, ModelCatalogSettings, list_api_models
from .profiles import ApiProfile, ApiProfileLibrary
from .prompts import SUMMARY_SYSTEM_PROMPT
from .protocols import PROTOCOL_LABELS
from .store import MeetingStore, MeetingSummary, TranscriptEntry, list_meetings, timestamp
from .templates import PromptTemplate, PromptTemplateLibrary, default_prompt_templates
from .worker import SummaryClient, SummaryWorker

__all__ = [
    "CHAT_SYSTEM_PROMPT",
    "PROTOCOL_LABELS",
    "SUMMARY_SYSTEM_PROMPT",
    "ApiProfile",
    "ApiProfileLibrary",
    "ChatClient",
    "ChatCoordinator",
    "ChatHistory",
    "ChatMessage",
    "ChatSummaryClient",
    "HttpSummaryClient",
    "MeetingStore",
    "MeetingSummary",
    "ModelCatalogError",
    "ModelCatalogSettings",
    "PromptTemplate",
    "PromptTemplateLibrary",
    "SummaryClient",
    "SummaryError",
    "SummarySettings",
    "SummaryWorker",
    "TranscriptEntry",
    "chat_context_usage",
    "create_summary_client",
    "default_prompt_templates",
    "delete_meeting",
    "list_api_models",
    "list_meetings",
    "meeting_for_deletion",
    "timestamp",
]
