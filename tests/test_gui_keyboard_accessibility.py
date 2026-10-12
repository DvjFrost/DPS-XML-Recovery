from __future__ import annotations

import tkinter as tk

import pytest

from dps_gui.app import DPSRecoveryApp


class FakeWorker:
    def poll_events(self):
        return []

    def close(self):
        self.closed = True


def test_real_tk_window_has_keyboard_bindings_visible_actions_and_clean_close(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk display unavailable: {error}")
    worker = FakeWorker()
    app = DPSRecoveryApp(root, worker=worker, portable_data=tmp_path / "дані")
    try:
        root.geometry("760x600")
        root.tk.call("tk", "scaling", 1.5)
        root.update()
        assert root.bind("<Control-o>")
        assert root.bind("<Control-r>")
        assert root.bind("<Return>")
        assert root.bind("<Escape>")
        for button in (app.view.export_button, app.view.stop_button, app.view.start_button):
            assert button.winfo_ismapped()
            assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
    finally:
        app.close()
    assert worker.closed is True
    with pytest.raises(tk.TclError):
        root.winfo_exists()
