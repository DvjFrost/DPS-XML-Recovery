"""Portable single-window Tk application."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .controller import AppState, InputValues, RecoveryController
from .portable_paths import PortableDataError, resolve_portable_data
from .safe_logging import SafeOperationalLogger, export_diagnostic_report
from .settings import GUISettings, load_settings, save_settings_atomic
from .worker_client import WorkerClient
from .views import MainView


def _base_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "dps_gui.entrypoint"]


class DPSRecoveryApp:
    POLL_MS = 100

    def __init__(self, root: tk.Tk, *, worker=None, diagnostic_runner: Callable | None = None, portable_data: Path | None = None) -> None:
        self.root = root
        self.worker = worker or WorkerClient()
        self.controller = RecoveryController(self.worker)
        self.diagnostic_runner = diagnostic_runner or self._run_diagnostic
        self.portable_data = Path(portable_data) if portable_data is not None else self._resolve_portable_data()
        self.settings_path = self.portable_data / "settings.json"
        self.operational_log = SafeOperationalLogger(self.portable_data / "logs")
        self.settings = load_settings(self.settings_path)
        self.view = MainView(root)
        self._apply_settings(self.settings)
        self._diagnostic_busy = False
        self._alias_labels: list[str] = []
        self._configure_root()
        self.view.bind_actions(
            check=self.check_iit,
            select_jks=self.select_jks,
            aliases=self.load_aliases,
            select_root=self.select_root,
            start=self.start,
            stop=self.stop,
            export=self.export_diagnostics,
            changed=self.validate,
        )
        root.bind("<Control-o>", lambda _event: self.select_jks())
        root.bind("<Control-r>", lambda _event: self.select_root())
        root.bind("<Return>", lambda _event: self.start() if self.controller.can_start else None)
        root.bind("<Escape>", lambda _event: self.stop() if self.controller.state is AppState.RUNNING else None)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_after_id = self.root.after(self.POLL_MS, self._poll)
        self.validate()
        self.operational_log.write("app_started", {"settings_loaded": self.settings_path.is_file()})

    def _resolve_portable_data(self) -> Path:
        executable_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
        try:
            return resolve_portable_data(executable_dir, None)
        except PortableDataError:
            selected = filedialog.askdirectory(title="Выберите доступный каталог portable-data")
            if not selected:
                raise
            return resolve_portable_data(executable_dir, Path(selected))

    def _apply_settings(self, settings: GUISettings) -> None:
        self.view.tax_id.set(settings.tax_id)
        self.view.start_year.set(str(settings.start_year))
        self.view.end_year.set(str(settings.end_year))
        self.view.root_path.set(settings.recovery_root)
        self.view.mode.set(settings.mode)
        self.view.remember_jks.set(settings.remember_jks)
        self.view.jks_path.set(settings.jks_path or "")

    def _current_settings(self) -> GUISettings:
        values = self._values()
        remember = self.view.remember_jks.get() is True
        return GUISettings(
            language="ru",
            tax_id=values.tax_id,
            start_year=values.start_year if values.start_year >= 2010 else 2020,
            end_year=values.end_year if values.end_year >= values.start_year else 2026,
            recovery_root=values.recovery_root,
            mode=values.mode,
            remember_jks=remember,
            jks_path=values.jks_path if remember else None,
        )

    def _configure_root(self) -> None:
        self.root.title("DPS XML Recovery")
        self.root.minsize(680, 480)
        self.root.geometry("760x700")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        try:
            self.root.tk.call("tk", "scaling", max(float(self.root.winfo_fpixels("1i")) / 72.0, 1.0))
        except tk.TclError:
            pass
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")

    @staticmethod
    def _run_diagnostic(kind: str, path: str | None = None) -> dict:
        command = [*_base_command(), f"--{kind}-json"]
        if path is not None:
            command.extend(["--jks-path", path])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode not in {0, 2}:
            return {"success": False}
        try:
            value = json.loads(result.stdout)
            return value if isinstance(value, dict) else {"success": False}
        except json.JSONDecodeError:
            return {"success": False}

    def _background_diagnostic(self, kind: str, path: str | None, callback: Callable[[dict], None]) -> None:
        if self._diagnostic_busy:
            return
        self._diagnostic_busy = True
        def work():
            try:
                result = self.diagnostic_runner(kind, path)
            except Exception:
                result = {"success": False}
            self.root.after(0, lambda: self._finish_diagnostic(result, callback))
        threading.Thread(target=work, name=f"gui-{kind}", daemon=True).start()

    def _finish_diagnostic(self, result: dict, callback: Callable[[dict], None]) -> None:
        self._diagnostic_busy = False
        callback(result)

    def check_iit(self) -> None:
        self.view.iit_status.set("Проверка комплекса ІІТ…")
        self._background_diagnostic("preflight", None, self._accept_preflight)

    def _accept_preflight(self, result: dict) -> None:
        success = result.get("success") is True
        message = "Готово к работе" if success else "ІІТ не готов: откройте подробности проверки"
        self.controller.set_preflight(success, message)
        self.view.iit_status.set(message)
        self.operational_log.write("iit_preflight", {"success": success})
        self.validate()

    def select_jks(self) -> None:
        value = filedialog.askopenfilename(title="Выберите файл ключа JKS", filetypes=(("JKS", "*.jks"), ("Все файлы", "*.*")))
        if value:
            self.view.jks_path.set(value)
            self.load_aliases()

    def load_aliases(self) -> None:
        path = self.view.jks_path.get().strip()
        if not Path(path).is_file():
            messagebox.showerror("JKS", "Сначала выберите существующий файл JKS.", parent=self.root)
            return
        self._background_diagnostic("aliases", path, self._accept_aliases)

    def _accept_aliases(self, result: dict) -> None:
        aliases = result.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            aliases = []
        self._alias_labels = aliases
        self.view.set_aliases(aliases)
        if not aliases:
            messagebox.showerror("JKS", "Не удалось получить список alias.", parent=self.root)
        self.validate()

    def select_root(self) -> None:
        value = filedialog.askdirectory(title="Выберите каталог сохранения XML")
        if value:
            self.view.root_path.set(value)

    def export_diagnostics(self) -> None:
        target = filedialog.asksaveasfilename(
            title="Экспорт безопасного диагностического отчёта",
            defaultextension=".json",
            filetypes=(("JSON", "*.json"),),
        )
        if not target:
            return
        try:
            export_diagnostic_report(
                Path(target),
                app={
                    "name": "DPS XML Recovery",
                    "python": sys.version.split()[0],
                    "platform": sys.platform,
                },
                settings=asdict(self._current_settings()),
                events=self.controller.diagnostic_events,
            )
            messagebox.showinfo("Диагностика", "Безопасный отчёт сохранён.", parent=self.root)
        except OSError:
            messagebox.showerror("Диагностика", "Не удалось сохранить отчёт.", parent=self.root)

    def _values(self) -> InputValues:
        try:
            start_year, end_year = int(self.view.start_year.get()), int(self.view.end_year.get())
        except ValueError:
            start_year = end_year = 0
        alias_text = self.view.alias.get().split(":", 1)[0].strip()
        try:
            alias_index = int(alias_text)
        except ValueError:
            alias_index = None
        return InputValues(
            self.view.tax_id.get(), self.view.jks_path.get(), alias_index,
            start_year, end_year, self.view.root_path.get(), self.view.mode.get(),
        )

    def validate(self) -> None:
        result = self.controller.set_inputs(self._values())
        self.view.form_status.set(next(iter(result.errors.values()), "Параметры проверены"))
        self._refresh()

    def start(self) -> None:
        self.validate()
        if not self.controller.can_start:
            messagebox.showerror("Проверка параметров", self.view.form_status.get(), parent=self.root)
            return
        password = bytearray()
        if self.controller.needs_password:
            value = self.view.password.get()
            self.view.password.set("")
            if not value:
                messagebox.showerror("КЭП", "Введите пароль КЭП.", parent=self.root)
                return
            password.extend(value.encode("utf-8"))
            value = None
        try:
            self.controller.start(password)
        except Exception as error:
            messagebox.showerror("Запуск", str(error), parent=self.root)
        self._refresh()

    def stop(self) -> None:
        confirmed = messagebox.askyesno("Остановка", "Безопасно остановить задание на ближайшей контрольной точке?", parent=self.root)
        self.controller.request_stop(confirmed)
        self._refresh()

    def _poll(self) -> None:
        self.controller.poll()
        self._refresh()
        self._poll_after_id = self.root.after(self.POLL_MS, self._poll)

    def _cancel_poll(self) -> None:
        identifier = self._poll_after_id
        self._poll_after_id = None
        if identifier is not None:
            try:
                self.root.after_cancel(identifier)
            except tk.TclError:
                pass

    def _refresh(self) -> None:
        running = self.controller.state in {AppState.RUNNING, AppState.STOPPING}
        self.view.start_button.configure(state="normal" if self.controller.can_start else "disabled")
        self.view.stop_button.configure(state="normal" if self.controller.state is AppState.RUNNING else "disabled")
        self.view.progress_text.set(self.controller.progress_phase or "Ожидание запуска")
        total = self.controller.progress_total
        self.view.progress_value.set((100 * self.controller.progress_current / total) if total > 0 else 0)
        self.view.result_text.set(self.controller.result_message)
        self.view.result_details.set(self.controller.result_details)
        self.view.set_log(self.controller.log_lines)
        for widget in (self.view.tax_entry, self.view.jks_entry, self.view.jks_button, self.view.remember_jks_button, self.view.alias_box, self.view.alias_button, self.view.password_entry, self.view.start_box, self.view.end_box, self.view.root_entry, self.view.root_button, self.view.advanced_button):
            widget.configure(state="disabled" if running else ("readonly" if widget is self.view.alias_box else "normal"))

    def close(self) -> None:
        if self.controller.state in {AppState.RUNNING, AppState.STOPPING}:
            if not messagebox.askyesno("Выход", "Задание выполняется. Остановить его и закрыть программу?", parent=self.root):
                return
        try:
            save_settings_atomic(self._current_settings(), self.settings_path)
            self.operational_log.write("app_closed", {"clean": True})
        except OSError:
            if not messagebox.askyesno(
                "Настройки",
                "Не удалось сохранить настройки. Всё равно закрыть программу?",
                parent=self.root,
            ):
                return
        self._cancel_poll()
        self.worker.close()
        self.root.destroy()


def run_app() -> int:
    root = tk.Tk()
    DPSRecoveryApp(root)
    root.mainloop()
    return 0


__all__ = ["DPSRecoveryApp", "run_app"]
