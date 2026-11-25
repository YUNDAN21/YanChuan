from livecaptions_backend.protocol import Router
from livecaptions_meetings import MeetingStore
from livecaptions_service import DesktopService


def test_export_retry_protocol_preserves_summary_cursor_and_reports_warning(tmp_path):
    events = []
    service = DesktopService(tmp_path, observer=events.append)
    service.store = MeetingStore.create(tmp_path / "data/meetings", "重试导出")
    entry = service.store.append("one", 0, 1, "已确认内容")
    service.store.save_summary([entry], "已完成总结", "fixture")
    original_export = service.store.export

    def fail():
        raise PermissionError("private path")

    service.store.export = fail
    try:
        router = Router(service)
        result = router.dispatch("meetings.export", {})
        assert not result["success"] and result["error"]
        assert "private path" not in result["error"]
        assert service.snapshot()["meeting"]["export_error"] == result["error"]
        assert events[-1].name == "summary.export"
        service.store.export = original_export
        assert router.dispatch("meetings.export", {}) == {"success": True, "error": ""}
        assert events[-1].payload == ""
        assert service.store.metadata["summary_cursor"] == 1
        assert len(service.store.summaries()) == 1
    finally:
        service.close()
