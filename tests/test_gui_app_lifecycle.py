from dps_gui.app import DPSRecoveryApp


class FakeRoot:
    def __init__(self):
        self.cancelled = []

    def after_cancel(self, identifier):
        self.cancelled.append(identifier)


def test_cancel_poll_is_idempotent_and_clears_pending_callback():
    app = DPSRecoveryApp.__new__(DPSRecoveryApp)
    app.root = FakeRoot()
    app._poll_after_id = "poll-17"
    app._cancel_poll()
    app._cancel_poll()
    assert app.root.cancelled == ["poll-17"]
    assert app._poll_after_id is None
