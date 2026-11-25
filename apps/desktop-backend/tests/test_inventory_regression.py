"""Slow inventory inspection must not occupy the JSONL control reader."""

import threading

from livecaptions_service import DesktopService


def test_slow_model_inventory_keeps_stop_control_responsive(tmp_path):
    from io import StringIO

    from livecaptions_backend.__main__ import serve

    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    class Service(DesktopService):
        def model_inventory(self):
            entered.set()
            assert release.wait(3)
            return {}

        def stop(self, **kwargs):
            stopped.set()
            return super().stop(**kwargs)

    requests = StringIO(
        '{"id":1,"method":"models.list"}\n'
        '{"id":2,"method":"session.stop"}\n'
        '{"id":3,"method":"shutdown"}\n'
    )
    thread = threading.Thread(
        target=serve, args=(requests, StringIO(), tmp_path), kwargs={"service_factory": Service}
    )
    thread.start()
    try:
        assert entered.wait(2)
        assert stopped.wait(1), "Inventory must not block stop"
    finally:
        release.set()
        thread.join(3)
    assert stopped.is_set()
