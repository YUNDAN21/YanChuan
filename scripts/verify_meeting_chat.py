"""Silent, real-service Q&A verification with synthetic meeting text and existing local credentials."""

import argparse
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from livecaptions_meetings import (
    CHAT_SYSTEM_PROMPT,
    ChatCoordinator,
    MeetingStore,
    create_summary_client,
)
from livecaptions_service.config import MeetingSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    settings = MeetingSettings.load(root / "data/meeting-settings.json")
    directory = (
        root
        / "data/verification"
        / ("meeting-chat-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    directory.mkdir(parents=True)
    report = {"silent": True, "device_opened": False, "synthetic_context": True, "protocols": []}
    for protocol in ["chat_completions", "responses", "anthropic"]:
        store = MeetingStore.create(directory / "meetings", "问答协议验证")
        store.append("one", 3, 8, "大家讨论交付与验收。" * 2000)
        entry = store.append("two", 9, 15, "本项目周五交付，小林负责验收。")
        store.rename("问答协议验证 · " + protocol)
        store.save_summary(store.entries(), "项目周五交付，小林负责验收。", "verification-fixture")
        client = create_summary_client(
            replace(
                settings.summary_settings(), protocol=protocol, system_prompt=CHAT_SYSTEM_PROMPT
            )
        )
        chat = ChatCoordinator()
        started = time.monotonic()
        try:
            for index, question in enumerate(
                [
                    "项目什么时候交付？请简短回答并引用时间戳。",
                    "谁负责验收？请结合刚才的问题回答，引用时间戳。",
                ],
                1,
            ):
                chat.send(store.directory, store.context_snapshot(), question, client)
                deadline = time.monotonic() + 120
                while True:
                    messages = chat.list(store.directory)
                    if messages[-1].status == "failed":
                        raise RuntimeError("真实聊天请求失败：" + protocol)
                    if len(messages) == index * 2 and messages[-1].status == "completed":
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError(protocol)
                    time.sleep(0.1)
            assert store.metadata["summary_cursor"] == entry.id
            report["protocols"].append(
                {
                    "protocol": protocol,
                    "model": client.model,
                    "source_characters": len(store.content()),
                    "answers": [m.text for m in messages if m.role == "assistant"],
                    "summary_cursor_unchanged": True,
                    "seconds": round(time.monotonic() - started, 2),
                }
            )
            print(protocol + ": two turns passed", flush=True)
        finally:
            chat.close()
            store.close()
            (directory / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
            )
    print("Report: " + str(directory / "report.json"), flush=True)


if __name__ == "__main__":
    main()
