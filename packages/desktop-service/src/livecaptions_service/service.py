"""Desktop use cases without a UI, network server, or process protocol."""

import logging
import threading
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from livecaptions_meetings import (
    CHAT_SYSTEM_PROMPT,
    ApiProfile,
    ApiProfileLibrary,
    ChatCoordinator,
    MeetingStore,
    ModelCatalogSettings,
    PromptTemplate,
    PromptTemplateLibrary,
    SummaryWorker,
    chat_context_usage,
    create_summary_client,
    default_prompt_templates,
    delete_meeting,
    list_api_models,
    list_meetings,
    meeting_for_deletion,
)
from livecaptions_translation import TranslationUnit, TranslationWorker

from livecaptions_asr import SubtitleSplitter
from livecaptions_asr.results import StreamEvent

from .appearance import AppearanceSettings
from .config import MeetingSettings
from .events import ServiceEvent
from .models import ModelManager, ModelSettings
from .presentation import CaptionHistory
from .runtime import DesktopRuntime, InputSelection
from .startup_trace import StartupTrace
from .translation import ApiTranslator, CaptionSettings

logger = logging.getLogger(__name__)


class DesktopService:
    def __init__(
        self,
        root: Path,
        *,
        runtime=None,
        observer=None,
        client_factory=None,
        translator_factory=None,
        chat_client_factory=None,
        model_manager_factory=ModelManager,
    ):
        self.root = Path(root).resolve()
        self.settings_path = self.root / "data/meeting-settings.json"
        self.settings = MeetingSettings.load(self.settings_path)
        self.appearance_path = self.root / "data/meeting-appearance.json"
        self.appearance = AppearanceSettings.load(self.appearance_path)
        self.runtime = runtime or DesktopRuntime(self.root)
        self.observer = observer or (lambda event: None)
        self.client_factory = client_factory or create_summary_client
        self.chat_client_factory = chat_client_factory or create_summary_client
        self.chat = ChatCoordinator(lambda payload: self._emit("chat.updated", payload))
        self.store = self.worker = None
        self.history = CaptionHistory()
        self._lock = threading.RLock()
        self._commands = threading.RLock()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread = None
        self._sequence = 0
        self.session_id = None
        self.mode = "idle"
        self.state = "idle"
        self.active = False
        self._closing = False
        self.summary_state = ""
        self.caption_settings_path = self.root / "data/caption-settings.json"
        self.caption_settings = CaptionSettings.load(self.caption_settings_path)
        self.translator_factory = translator_factory or ApiTranslator
        self.translation_worker = None
        self.translation_units = {}
        self.translations = {}
        self.translation_state = ""
        self._translation_fingerprint = None
        self._source_language = "auto"
        self._translation_available = False
        self.models = model_manager_factory(
            self.root, lambda payload: self._emit("models.download", payload)
        )

    def model_inventory(self):
        return self.models.inventory()

    def api_models(self, base_url, api_key="", protocol="chat_completions"):
        """Discover from an editor draft without saving it or changing active clients."""
        return list_api_models(ModelCatalogSettings(base_url, api_key, protocol))

    def configure_models(
        self, selected_model, source="huggingface", directory=None, model_file=None
    ):
        with self._commands:
            if self.active:
                raise RuntimeError("请先停止转写，再切换模型或目录")
            settings = ModelSettings(selected_model, source, directory, model_file)
            self.models.configure(settings)
            if hasattr(self.runtime, "configure_models"):
                self.runtime.configure_models(settings)
            return self.models.inventory()

    def download_models(self):
        with self._commands:
            return self.models.start()

    def _emit(self, name, payload):
        with self._lock:
            self._sequence += 1
            event = ServiceEvent(self._sequence, self.session_id, name, payload)
            try:
                self.observer(event)
            except Exception:  # noqa: BLE001 - observers cannot stop a recognition session
                logger.error("Service observer failed")

    def _state(self, value):
        with self._lock:
            self.state = value
            self._emit("state", {"state": value, "mode": self.mode, "active": self.active})

    def _error(self, error):
        message = str(error)
        for profile in self.settings.api_profiles:
            if profile.api_key:
                message = message.replace(profile.api_key, "[redacted]")
        return message

    def configuration(self):
        """Local configuration, including credentials, for a trusted settings editor only."""
        with self._lock:
            return {
                **asdict(self.settings),
                "appearance": self.appearance.mode,
                "captions": asdict(self.caption_settings),
            }

    def snapshot(self):
        with self._lock:
            return {
                "sequence": self._sequence,
                "session_id": self.session_id,
                "state": self.state,
                "mode": self.mode,
                "active": self.active,
                "summary_busy": bool(self.worker and self.worker.busy),
                "summary_state": self.summary_state,
                "meeting": self._meeting(),
                "captions": [asdict(item) for item in self.history.confirmed],
                "pending": asdict(self.history.pending) if self.history.pending else None,
                "translation_units": [asdict(item) for item in self.translation_units.values()],
                "translations": [asdict(item) for item in self.translations.values()],
                "translation_state": self.translation_state,
            }

    def _meeting(self):
        if self.store is None:
            return None
        return {
            "directory": str(self.store.directory),
            "metadata": self.store.metadata,
            "entries": [asdict(item) for item in self.store.entries()],
            "summaries": [asdict(item) for item in self.store.summaries()],
            "summary_errors": self.store.summary_errors(),
        }

    def list_meetings(self):
        return list_meetings(self.root / "data/meetings")

    def devices(self):
        from livecaptions_audio import list_devices

        return list_devices()

    def _retire(self):
        if self.active or (self.worker and self.worker.busy):
            raise RuntimeError("请先结束当前任务或等待本次总结完成")
        if self.worker and self.worker.running:
            self.worker.close(drain=False)
            self.worker.wait()
        if self.store:
            self.store.close()
        self.store = self.worker = None

    def _summary_update(self, kind, value):
        if kind == "error":
            self.summary_state = self._error(value)
        elif kind == "summary":
            self.summary_state = "总结已保存"
        elif kind == "busy" and value:
            self.summary_state = "正在总结…"
        self._emit("summary." + kind, asdict(value) if kind == "summary" else value)

    def _ensure_worker(self, *, automatic=False):
        with self._commands:
            return self._create_worker(automatic=automatic)

    def _create_worker(self, *, automatic=False):
        if self.worker and self.worker.running:
            return self.worker
        client = self.client_factory(self.settings.summary_settings())
        self.worker = SummaryWorker(
            self.store,
            client,
            interval_seconds=self.settings.interval_minutes * 60,
            automatic=automatic,
            on_update=self._summary_update,
        )
        self.worker.start()
        return self.worker

    def start(self, mode="meeting", *, title="", selection=None, file=None):
        if mode not in {"meeting", "captions", "file"}:
            raise ValueError("不支持的任务类型")
        selection = selection or InputSelection(source="both" if mode == "meeting" else "system")
        if mode == "file":
            file = Path(file).resolve()
            if not file.is_file() or file.suffix.lower() not in {".wav", ".flac"}:
                raise ValueError("请选择 WAV 或 FLAC 录音")
        elif file is not None:
            raise ValueError("文件输入须使用 file 任务类型")
        with self._commands:
            if self._closing:
                raise RuntimeError("应用正在退出")
            self._retire()
            self.mode, self.session_id = mode, uuid4().hex
            self._source_language = selection.language
            if self.translation_worker:
                self.translation_worker.configure(
                    self.translation_worker.translator, self.caption_settings.target_language
                )
            if mode != "captions":
                self.store = MeetingStore.create(self.root / "data/meetings", title)
                self._emit("meeting.loaded", self._meeting())
            self.history.begin_session()
            self._emit("captions", {"confirmed": [], "pending": None})
            self._stop.clear()
            self._cancel.clear()
            self.active = True
            self._state("preparing")
            self._thread = threading.Thread(
                target=self._run,
                args=(selection, file),
                name="desktop-transcription",
                daemon=True,
            )
            self._thread.start()
            return {"session_id": self.session_id}

    def _ingest(self, batch):
        with self._lock:
            for event in batch:
                if self.store and event.sentence is not None:
                    item = event.sentence
                    entry = self.store.append(
                        f"live:{item.id}",
                        item.start_seconds,
                        item.end_seconds,
                        item.text,
                    )
                    if entry:
                        self._emit("transcript", asdict(entry))
            self._project_captions(batch)

    def _project_captions(self, batch):
        with self._lock:
            before = len(self.history.confirmed)
            for event in batch:
                self.history.apply(event)
                if event.segment and event.segment.language not in {"unknown", "auto"}:
                    self._source_language = event.segment.language
            pending = self.history.pending
            self._emit(
                "captions",
                {
                    "confirmed": [asdict(item) for item in self.history.confirmed[before:]],
                    "pending": asdict(pending) if pending else None,
                },
            )
            if self.mode == "captions":
                self._translate_captions(self.history.confirmed[before:], pending)

    def _translation_update(self, result):
        with self._lock:
            if (
                not self.translation_worker
                or result.generation != self.translation_worker.generation
            ):
                return
            unit = self.translation_units.get(result.key)
            if unit is None or unit.text != result.source_text:
                return
            if result.target_language != self.caption_settings.target_language:
                return
            result = replace(result, error=self._error(result.error))
            self.translations[result.key] = result
            self.translation_state = result.error or ""
            self._emit("translation", asdict(result))

    def _sync_translation(self):
        settings = self.caption_settings
        profile_id = settings.api_profile_id or self.settings.selected_api_profile
        profile = next((p for p in self.settings.api_profiles if p.id == profile_id), None)
        fingerprint = (
            settings.enabled,
            settings.display_mode != "original",
            settings.target_language,
            profile,
        )
        if fingerprint == self._translation_fingerprint:
            return
        self._translation_fingerprint = fingerprint
        self._translation_available = False
        self.translations.clear()
        if self.translation_worker:
            self.translation_worker.configure(
                self.translation_worker.translator, settings.target_language
            )
        self._emit("translation.reset", None)
        if not settings.enabled or settings.display_mode == "original":
            self.translation_state = ""
            return
        try:
            if profile is None:
                raise ValueError("翻译 API 配置不存在，请选择其他服务")
            translator = self.translator_factory(profile)
            if self.translation_worker is None:
                self.translation_worker = TranslationWorker(
                    translator, settings.target_language, on_update=self._translation_update
                )
            else:
                self.translation_worker.configure(translator, settings.target_language)
            self.translation_state = ""
            self._translation_available = True
        except ValueError:
            self.translation_state = "请在 API 配置中设置翻译模型和密钥"
            self._emit("translation.status", self.translation_state)

    def _translate_captions(self, confirmed, pending):
        self._sync_translation()
        removed = [key for key, unit in self.translation_units.items() if unit.pending]
        for key in removed:
            self.translation_units.pop(key, None)
        units = []
        for entry in list(confirmed) + ([pending] if pending else []):
            for cue in SubtitleSplitter(32).split(entry.text):
                unit = TranslationUnit(
                    f"{entry.key}:{cue.start}",
                    cue.text.strip(),
                    entry.start_seconds,
                    self._source_language,
                    entry.pending,
                )
                self.translation_units[unit.key] = unit
                units.append(unit)
        self._emit("translation.units", {"units": [asdict(u) for u in units], "removed": removed})
        if self.translation_worker:
            self.translation_worker.reset_pending([u.key for u in units if u.pending])
            if (
                self.caption_settings.enabled
                and self.caption_settings.display_mode != "original"
                and self._translation_available
            ):
                for unit in units:
                    self.translation_worker.submit(unit)

    def configure_captions(self, settings):
        settings = (
            settings if isinstance(settings, CaptionSettings) else CaptionSettings(**settings)
        )
        if settings.enabled and settings.display_mode != "original":
            profile_id = settings.api_profile_id or self.settings.selected_api_profile
            profile = next((p for p in self.settings.api_profiles if p.id == profile_id), None)
            if profile is None:
                raise ValueError("翻译 API 配置不存在")
            self.translator_factory(profile)
        with self._commands, self._lock:
            settings.save(self.caption_settings_path)
            self.caption_settings = settings
            self._sync_translation()
            self._emit("configuration", self.configuration())
            if self.translation_worker and settings.enabled and settings.display_mode != "original":
                for unit in list(self.translation_units.values())[-8:]:
                    self.translation_worker.submit(unit)

    def _run(self, selection, file):
        trace = StartupTrace(
            self.root / "data/desktop/startup.jsonl",
            lambda value: self._emit("progress", value.to_dict()),
        )
        self.runtime.on_progress = trace.emit
        error = None

        def ready(state):
            if state == "recording":
                trace.finish()
                if self.store:
                    self.store.set_status("recording")
                    if self.settings.automatic_summary:
                        try:
                            self._ensure_worker(automatic=True)
                        except ValueError as exc:
                            self._summary_update("error", str(exc))
            self._state(state)

        try:
            if file is None:
                self.runtime.runner(selection).run(
                    selection.language,
                    self._stop,
                    self._cancel,
                    self._ingest,
                    ready,
                    on_progress=trace.emit,
                )
            else:
                recognizer = self.runtime.recognizer()
                recognizer.prepare(on_progress=trace.emit)
                if not self._stop.is_set():
                    ready("recording")

                    def segment(value):
                        item = value.segment
                        with self._lock:
                            entry = self.store.append(
                                f"file:{item.id}",
                                item.start_seconds,
                                item.end_seconds,
                                item.text,
                            )
                            if entry:
                                self._emit("transcript", asdict(entry))
                            self._project_captions(
                                [StreamEvent("final", segment=item)]
                                + [
                                    StreamEvent("sentence", sentence=part)
                                    for part in value.semantic_sections
                                ]
                            )
                            self._emit(
                                "file.progress",
                                {
                                    "processed_seconds": value.processed_seconds,
                                    "chunks_completed": value.chunks_completed,
                                },
                            )

                    result = recognizer.transcribe_file(
                        file,
                        selection.language,
                        on_segment=segment,
                        cancel=self._stop,
                    )
                    self._project_captions(
                        [
                            StreamEvent("sentence", sentence=part)
                            for part in result.semantic_sections
                        ]
                    )
        except Exception as exc:  # noqa: BLE001 - isolate model/device adapter failures
            if getattr(exc, "code", None) != "cancelled":
                error = self._error(exc)
                self._emit("error", error)
        finally:
            trace.finish("failed" if error else "completed")
            try:
                if self.store:
                    self.store.set_status("error" if error else "ended", ended=True)
                    self.store.export()
                    # Explicitly disabled automatic mode must remain manual at the end.
                    if self.worker or self.settings.automatic_summary:
                        try:
                            worker = self._ensure_worker()
                            self._state("summarizing")
                            worker.close(drain=True)
                            worker.wait()
                        except ValueError as exc:
                            self._summary_update("error", str(exc))
            except Exception as exc:  # noqa: BLE001 - always release session ownership
                error = self._error(exc)
                self._emit("error", error)
            finally:
                self.runtime.on_progress = None
                with self._lock:
                    self.active = False
                    self._state("error" if error else "ended")
                    self._emit("finished", None)

    def stop(self, *, cancel=False):
        if cancel:
            self._cancel.set()
        self._stop.set()
        if self.active:
            self._state("draining")

    def wait(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)
        return not self.active

    def open_meeting(self, directory):
        directory = Path(directory).resolve()
        if directory.parent != (self.root / "data/meetings").resolve():
            raise ValueError("会议记录不在当前项目的记录目录中")
        if not (directory / "meeting.sqlite3").is_file():
            raise ValueError("会议记录不存在")
        with self._commands:
            self._retire()
            self.store = MeetingStore(directory)
            self.store.export()
            self._emit("meeting.loaded", self._meeting())
            return self._meeting()

    def delete_meeting(self, directory):
        with self._commands:
            root = self.root / "data/meetings"
            metadata = meeting_for_deletion(root, directory)
            current = bool(
                self.store and self.store.directory.resolve() == Path(metadata["directory"])
            )
            if current:
                self._retire()
            self.chat.forget(metadata["id"], metadata["directory"])
            try:
                deleted = delete_meeting(root, metadata["directory"])
            except OSError:
                if current and Path(metadata["directory"], "meeting.sqlite3").is_file():
                    self.store = MeetingStore(Path(metadata["directory"]))
                    self._emit("meeting.loaded", self._meeting())
                raise RuntimeError("删除失败，请关闭占用会议文件的程序后重试") from None
            if current:
                self.summary_state = ""
                self.mode = "idle"
                self.session_id = None
                self._state("idle")
            self._emit("meeting.deleted", {**deleted, "current": current})
            return {**deleted, "current": current}

    def summarize_now(self):
        with self._commands:
            if self.store is None:
                return False
            return self._ensure_worker().request()

    def rename_meeting(self, title):
        with self._commands:
            if self.store is None:
                raise ValueError("请先打开会议")
            metadata = self.store.rename(title)
            self._emit("meeting.renamed", metadata)
            return metadata

    def meeting_content(self, kind="all"):
        with self._commands:
            if self.store is None:
                raise ValueError("请先打开会议")
            return {"text": self.store.content(kind)}

    def chat_messages(self):
        with self._commands:
            if self.store is None:
                return {"meeting_id": None, "messages": []}
            return {
                "meeting_id": self.store.metadata["id"],
                "messages": [asdict(m) for m in self.chat.list(self.store.directory)],
            }

    def send_chat(self, question):
        with self._commands:
            if self.store is None:
                raise ValueError("请先打开或开始会议")
            selected = self.settings.chat_api_profile or self.settings.selected_api_profile
            profile = next((p for p in self.settings.api_profiles if p.id == selected), None)
            if profile is None:
                raise ValueError("聊天 API 已被删除，请重新选择")
            settings = replace(
                replace(profile, model=self.settings.chat_model or profile.model).summary_settings(
                    CHAT_SYSTEM_PROMPT
                ),
                reasoning_effort=self.settings.chat_reasoning_effort,
            )
            client = self.chat_client_factory(settings)
            result = self.chat.send(
                self.store.directory,
                self.store.context_snapshot(),
                question,
                client,
                context_tokens=self.settings.chat_context_tokens,
            )
            if self.settings.chat_api_profile is None:
                self._save_settings(replace(self.settings, chat_api_profile=selected))
            return result

    def chat_usage(self, question=""):
        with self._commands:
            meeting_id = self.store.metadata["id"] if self.store else None
            context = self.store.context_snapshot()["content"] if self.store else ""
            history = self.chat.list(self.store.directory) if self.store else []
            return {
                "meeting_id": meeting_id,
                **chat_context_usage(
                    context, history, question, context_tokens=self.settings.chat_context_tokens
                ),
            }

    def cancel_chat(self, request_id=None):
        with self._commands:
            return bool(self.store and self.chat.cancel(self.store.metadata["id"], request_id))

    def configure_chat(
        self, api_profile_id=None, context_tokens=None, *, model=None, reasoning_effort=None
    ):
        with self._commands:
            current = self.settings.chat_api_profile or self.settings.selected_api_profile
            api_profile_id = api_profile_id or current
            if not any(p.id == api_profile_id for p in self.settings.api_profiles):
                raise ValueError("请选择已有的聊天 API 配置")
            changes = {"chat_api_profile": api_profile_id}
            if context_tokens is not None:
                changes["chat_context_tokens"] = context_tokens
            if model is not None:
                if not isinstance(model, str):
                    raise ValueError("问答模型名称无效")
                changes["chat_model"] = model.strip()
            elif api_profile_id != current:
                changes["chat_model"] = ""
            if reasoning_effort is not None:
                changes["chat_reasoning_effort"] = reasoning_effort
            self._save_settings(replace(self.settings, **changes))

    def _save_settings(self, settings, *, require_summary=False):
        client = None
        if require_summary or (self.worker and self.worker.running):
            client = self.client_factory(settings.summary_settings())
        settings.save(self.settings_path)
        self.settings = settings
        if client and self.worker:
            self.worker.set_client(client)
        self._emit("configuration", self.configuration())
        with self._lock:
            self._sync_translation()

    def configure_schedule(self, minutes, automatic):
        if isinstance(minutes, bool) or int(minutes) != minutes:
            raise ValueError("总结间隔须为整数分钟")
        with self._commands:
            self._save_settings(
                replace(
                    self.settings, interval_minutes=int(minutes), automatic_summary=bool(automatic)
                )
            )
            if self.worker and self.worker.running:
                self.worker.configure(minutes * 60, automatic and self.active)
            elif automatic and self.active and self.store:
                self._ensure_worker(automatic=True)

    def set_api_profiles(self, profiles, selected_id):
        library = ApiProfileLibrary(
            [item if isinstance(item, ApiProfile) else ApiProfile(**item) for item in profiles],
            selected_id,
        )
        with self._commands:
            self._save_settings(
                replace(
                    self.settings, api_profiles=library.profiles, selected_api_profile=selected_id
                ),
                require_summary=True,
            )

    def select_api_profile(self, profile_id):
        self.set_api_profiles(self.settings.api_profiles, profile_id)

    def set_templates(self, templates, selected_id):
        library = PromptTemplateLibrary(
            [
                item if isinstance(item, PromptTemplate) else PromptTemplate(**item)
                for item in templates
            ],
            selected_id,
        )
        with self._commands:
            self._save_settings(
                replace(
                    self.settings,
                    prompt_templates=library.templates,
                    selected_prompt_template=selected_id,
                )
            )

    def default_templates(self):
        return [asdict(item) for item in default_prompt_templates()]

    def set_appearance(self, mode):
        with self._lock:
            appearance = AppearanceSettings(mode)
            appearance.save(self.appearance_path)
            self.appearance = appearance
            self._emit("appearance", mode)

    def close(self):
        with self._commands:
            self._closing = True
        self.stop()
        self.models.close()
        self.chat.close()
        self.wait()
        # Never hold the service lock while joining a worker that publishes events.
        if self.worker and self.worker.running:
            self.worker.close(drain=False)
            self.worker.wait()
        if self.translation_worker:
            self.translation_worker.close()
        with self._commands:
            self._retire()
        close_runtime = getattr(self.runtime, "close", None)
        if close_runtime is not None:
            close_runtime()
