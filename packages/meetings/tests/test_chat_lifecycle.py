import threading

import pytest
from livecaptions_meetings import ChatCoordinator, MeetingStore
from test_chat import Client, wait
from test_chat_compression import Compressor, seed


@pytest.mark.parametrize("kind", ["answer", "compression"])
def test_thread_start_failure_releases_request_and_allows_retry(tmp_path, monkeypatch, kind):
    store = MeetingStore.create(tmp_path, "启动失败")
    coordinator = ChatCoordinator()
    if kind == "compression":
        seed(store.directory)

    def submit():
        if kind == "compression":
            return coordinator.compress(store.directory, store.metadata["id"], Compressor())
        return coordinator.send(store.directory, store.context_snapshot(), "问题", Client())

    def unavailable(thread):
        raise RuntimeError("cannot start thread")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", unavailable)
            with pytest.raises(RuntimeError, match="cannot start thread"):
                submit()
        assert not coordinator.cancel(store.metadata["id"])
        assert not coordinator._workers
        state = (
            coordinator.context(store.directory)["status"]
            if kind == "compression"
            else coordinator.list(store.directory)[-1].status
        )
        assert state == "failed"
        submit()
        wait(lambda: not coordinator._workers)
        state = (
            coordinator.context(store.directory)["status"]
            if kind == "compression"
            else coordinator.list(store.directory)[-1].status
        )
        assert state == "completed"
    finally:
        coordinator.close()
        store.close()


def test_cancelled_compression_cannot_finish_a_new_answer(tmp_path):
    store = MeetingStore.create(tmp_path, "任务归属")
    original = seed(store.directory)
    compressor, answer = Compressor(block=True), Client(block=True)
    coordinator = ChatCoordinator()
    try:
        old = coordinator.compress(store.directory, store.metadata["id"], compressor)
        assert compressor.entered.wait(2)
        assert coordinator.cancel(store.metadata["id"], old["request_id"])
        current = coordinator.send(store.directory, store.context_snapshot(), "追问", answer)
        assert answer.entered.wait(2)
        compressor.release.set()
        wait(lambda: old["request_id"] not in coordinator._workers)
        assert coordinator.list(store.directory)[-1].status == "pending"
        assert not coordinator.context(store.directory)["summary"]
        assert not coordinator.cancel(store.metadata["id"], old["request_id"])
        answer.release.set()
        wait(lambda: current["request_id"] not in coordinator._workers)
        records = coordinator.list(store.directory)
        assert records[: len(original)] == original
        assert records[-1].status == "completed"
        assert coordinator.context(store.directory)["status"] == "cancelled"
    finally:
        compressor.release.set()
        answer.release.set()
        coordinator.close()
        store.close()


@pytest.mark.parametrize("kind", ["answer", "compression"])
def test_deadline_unblocks_retry_and_drops_late_result(tmp_path, kind):
    store = MeetingStore.create(tmp_path, "超时")
    coordinator = ChatCoordinator(task_timeout=0.08)
    blocked = Compressor(block=True) if kind == "compression" else Client(block=True)
    if kind == "compression":
        seed(store.directory)
    try:
        if kind == "compression":
            request = coordinator.compress(store.directory, store.metadata["id"], blocked)
            wait(lambda: coordinator.context(store.directory)["status"] == "failed")
            assert not coordinator.context(store.directory)["summary"]
        else:
            request = coordinator.send(store.directory, store.context_snapshot(), "问题", blocked)
            wait(lambda: coordinator.list(store.directory)[-1].status == "failed")
            assert "超时" in coordinator.list(store.directory)[-1].error
        newer = coordinator.send(store.directory, store.context_snapshot(), "重试", Client())
        wait(lambda: newer["request_id"] not in coordinator._workers)
        blocked.release.set()
        wait(lambda: request["request_id"] not in coordinator._workers)
        assert coordinator.list(store.directory)[-1].status == "completed"
    finally:
        blocked.release.set()
        coordinator.close()
        store.close()
