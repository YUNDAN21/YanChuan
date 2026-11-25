"""Persistent meeting notes and summaries, usable without a UI or recognition model."""

from .agent_catalog import AgentCatalog, AgentModel, local_agent_catalog
from .chat import (
    CHAT_SYSTEM_PROMPT,
    ChatClient,
    ChatCoordinator,
    ChatHistory,
    ChatMessage,
    chat_context_usage,
)
from .chat_context import COMPACTION_PROMPT
from .client import (
    ChatSummaryClient,
    HttpSummaryClient,
    SummaryError,
    SummarySettings,
    create_summary_client,
)
from .deletion import delete_meeting, meeting_for_deletion
from .local_agent import LocalAgentClient, LocalAgentSettings, local_agent_version
from .meeting_agent import AGENT_PROMPT, MeetingAgent
from .meeting_reference import MeetingReference
from .model_catalog import ModelCatalogError, ModelCatalogSettings, list_api_models
from .notes import NoteStore
from .profiles import ApiProfile, ApiProfileLibrary
from .prompts import SUMMARY_SYSTEM_PROMPT
from .protocols import PROTOCOL_LABELS
from .store import MeetingStore, MeetingSummary, TranscriptEntry, list_meetings, timestamp
from .templates import PromptTemplate, PromptTemplateLibrary, default_prompt_templates
from .worker import SummaryClient, SummaryWorker

__all__ = [
    "AGENT_PROMPT",
    "CHAT_SYSTEM_PROMPT",
    "COMPACTION_PROMPT",
    "PROTOCOL_LABELS",
    "SUMMARY_SYSTEM_PROMPT",
    "AgentCatalog",
    "AgentModel",
    "ApiProfile",
    "ApiProfileLibrary",
    "ChatClient",
    "ChatCoordinator",
    "ChatHistory",
    "ChatMessage",
    "ChatSummaryClient",
    "HttpSummaryClient",
    "LocalAgentClient",
    "LocalAgentSettings",
    "MeetingAgent",
    "MeetingReference",
    "MeetingStore",
    "MeetingSummary",
    "ModelCatalogError",
    "ModelCatalogSettings",
    "NoteStore",
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
    "local_agent_catalog",
    "local_agent_version",
    "meeting_for_deletion",
    "timestamp",
]
