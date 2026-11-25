from types import SimpleNamespace

from livecaptions_meetings import MeetingStore, SummaryWorker


def test_committed_summary_is_published_when_export_fails_and_retry_is_local(tmp_path):
    store = MeetingStore.create(tmp_path, "导出重试")
    store.append("one", 0, 1, "完整会议记录")
    original_export = store.export
    calls, events = [], []

    def fail():
        raise OSError("private transport secret")

    def summarize(*args):
        calls.append(args)
        return "已保存总结"

    store.export = fail
    worker = SummaryWorker(
        store,
        SimpleNamespace(model="fixture", summarize=summarize),
        automatic=False,
        on_update=lambda kind, value: events.append((kind, value)),
    )
    worker.start()
    try:
        worker.close(drain=True)
        worker.wait(3)
        assert not worker.running and not worker.last_error
        assert store.metadata["summary_cursor"] == 1
        assert store.summaries()[0].text == "已保存总结"
        assert any(kind == "summary" for kind, _ in events)
        assert any(kind == "export" and value for kind, value in events)
        assert not store.summary_errors()
        assert not worker.request()
        assert "secret" not in store.export_error
        assert not store.retry_export()
        store.export = original_export
        assert store.retry_export() and not store.export_error
        assert "已保存总结" in (store.directory / "summaries.md").read_text("utf-8")
        assert len(calls) == 1
        directory = store.directory
    finally:
        store.close()
    reopened = MeetingStore(directory)
    try:
        assert reopened.metadata["summary_cursor"] == 1
        assert reopened.summaries()[0].text == "已保存总结"
    finally:
        reopened.close()
