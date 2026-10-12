"""One-window Tkinter/ttk view with no recovery or crypto logic."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .resources import strings


class MainView(ttk.Frame):
    def __init__(self, master: tk.Misc, *, language: str = "ru") -> None:
        super().__init__(master, padding=16)
        self.text = strings(language)
        self.tax_id = tk.StringVar()
        self.jks_path = tk.StringVar()
        self.alias = tk.StringVar(value="0")
        self.password = tk.StringVar()
        self.start_year = tk.StringVar(value="2020")
        self.end_year = tk.StringVar(value="2026")
        self.root_path = tk.StringVar()
        self.mode = tk.StringVar(value="full")
        self.iit_status = tk.StringVar(value="Проверка не выполнена")
        self.form_status = tk.StringVar(value="Заполните параметры")
        self.progress_text = tk.StringVar(value="Ожидание запуска")
        self.result_text = tk.StringVar()
        self.result_details = tk.StringVar()
        self.progress_value = tk.DoubleVar(value=0)
        self.advanced_visible = tk.BooleanVar(value=False)
        self.remember_jks = tk.BooleanVar(value=False)
        self._build()

    def _label_entry(self, parent, row, label, variable, **entry_options):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(5, 2))
        widget = ttk.Entry(parent, textvariable=variable, **entry_options)
        widget.grid(row=row + 1, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        return widget

    def _build(self) -> None:
        self.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        content = ttk.Frame(self.canvas, padding=16)
        content.columnconfigure(0, weight=1)
        self._canvas_window = self.canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self._canvas_window, width=event.width))
        self.canvas.bind_all("<MouseWheel>", self._mousewheel)

        header = ttk.Frame(content)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=self.text["title"], font=("Segoe UI", 15, "bold")).pack(side="left")

        system = ttk.LabelFrame(content, text=self.text["iit"], padding=10)
        system.grid(row=1, column=0, sticky="ew", pady=(12, 6))
        system.columnconfigure(0, weight=1)
        ttk.Label(system, textvariable=self.iit_status).grid(row=0, column=0, sticky="w")
        self.check_button = ttk.Button(system, text="Проверить ІІТ")
        self.check_button.grid(row=0, column=1, padx=(8, 0))

        form = ttk.LabelFrame(content, text="Параметры восстановления", padding=10)
        form.grid(row=2, column=0, sticky="nsew", pady=6)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, weight=0)
        self.tax_entry = self._label_entry(form, 0, self.text["tax_id"], self.tax_id)
        ttk.Label(form, text=self.text["jks"]).grid(row=2, column=0, sticky="w", pady=(5, 2))
        self.jks_entry = ttk.Entry(form, textvariable=self.jks_path)
        self.jks_entry.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.jks_button = ttk.Button(form, text="Обзор…")
        self.jks_button.grid(row=3, column=2, padx=(8, 0))
        self.remember_jks_button = ttk.Checkbutton(
            form, text="Запомнить путь к JKS (пароль не сохраняется)", variable=self.remember_jks
        )
        self.remember_jks_button.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(form, text=self.text["alias"]).grid(row=5, column=0, sticky="w", pady=(8, 2))
        self.alias_box = ttk.Combobox(form, textvariable=self.alias, values=("0",), state="readonly", width=24)
        self.alias_box.grid(row=6, column=0, sticky="ew")
        self.alias_button = ttk.Button(form, text="Обновить alias")
        self.alias_button.grid(row=6, column=1, sticky="w", padx=(8, 0))
        ttk.Label(form, text="Пароль КЭП").grid(row=7, column=0, sticky="w", pady=(8, 2))
        self.password_entry = ttk.Entry(form, textvariable=self.password, show="●")
        self.password_entry.grid(row=8, column=0, columnspan=3, sticky="ew")

        ttk.Label(form, text=self.text["period"]).grid(row=9, column=0, sticky="w", pady=(8, 2))
        years = ttk.Frame(form)
        years.grid(row=10, column=0, columnspan=3, sticky="ew")
        ttk.Label(years, text="С").pack(side="left")
        self.start_box = ttk.Spinbox(years, from_=2010, to=2100, textvariable=self.start_year, width=8)
        self.start_box.pack(side="left", padx=(6, 18))
        ttk.Label(years, text="по").pack(side="left")
        self.end_box = ttk.Spinbox(years, from_=2010, to=2100, textvariable=self.end_year, width=8)
        self.end_box.pack(side="left", padx=6)

        ttk.Label(form, text=self.text["root"]).grid(row=11, column=0, sticky="w", pady=(8, 2))
        self.root_entry = ttk.Entry(form, textvariable=self.root_path)
        self.root_entry.grid(row=12, column=0, columnspan=2, sticky="ew")
        self.root_button = ttk.Button(form, text="Обзор…")
        self.root_button.grid(row=12, column=2, padx=(8, 0))

        ttk.Label(form, text=self.text["mode"]).grid(row=13, column=0, sticky="w", pady=(8, 2))
        modes = ttk.Frame(form)
        modes.grid(row=14, column=0, columnspan=3, sticky="w")
        for label, value in (("Полное восстановление", "full"), ("Только inventory", "inventory_only"), ("Только аудит", "audit_only")):
            ttk.Radiobutton(modes, text=label, value=value, variable=self.mode).pack(side="left", padx=(0, 12))

        self.advanced_button = ttk.Checkbutton(
            form, text=self.text["advanced"], variable=self.advanced_visible, command=self._toggle_advanced
        )
        self.advanced_button.grid(row=15, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.advanced = ttk.Frame(form)
        ttk.Radiobutton(self.advanced, text="Только проверка API", value="probe_only", variable=self.mode).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(self.advanced, text="Перестроить индекс и проверить", value="reindex_and_audit", variable=self.mode).pack(side="left")
        ttk.Label(form, textvariable=self.form_status, foreground="#8a4b00").grid(row=17, column=0, columnspan=3, sticky="w", pady=(8, 0))

        progress = ttk.LabelFrame(content, text="Ход выполнения", padding=10)
        progress.grid(row=3, column=0, sticky="ew", pady=6)
        progress.columnconfigure(0, weight=1)
        ttk.Label(progress, textvariable=self.progress_text).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(progress, variable=self.progress_value, maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(6, 4))
        self.log = tk.Text(progress, height=5, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.grid(row=2, column=0, sticky="ew")
        ttk.Label(progress, textvariable=self.result_text, font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Label(progress, textvariable=self.result_details, wraplength=650).grid(row=4, column=0, sticky="w", pady=(2, 0))

        actions = ttk.Frame(self, padding=(16, 8, 16, 12))
        actions.grid(row=1, column=0, columnspan=2, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self.export_button = ttk.Button(actions, text="Экспорт диагностики…")
        self.export_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(actions, text=self.text["stop"], state="disabled")
        self.stop_button.grid(row=0, column=1, padx=(0, 8))
        self.start_button = ttk.Button(actions, text=self.text["start"], state="disabled", default="active")
        self.start_button.grid(row=0, column=2)

    def _mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _toggle_advanced(self) -> None:
        if self.advanced_visible.get():
            self.advanced.grid(row=16, column=0, columnspan=3, sticky="w", pady=(4, 0))
        else:
            self.advanced.grid_remove()
            if self.mode.get() in {"probe_only", "reindex_and_audit"}:
                self.mode.set("full")

    def bind_actions(self, *, check: Callable, select_jks: Callable, aliases: Callable, select_root: Callable, start: Callable, stop: Callable, export: Callable, changed: Callable) -> None:
        self.check_button.configure(command=check)
        self.jks_button.configure(command=select_jks)
        self.alias_button.configure(command=aliases)
        self.root_button.configure(command=select_root)
        self.start_button.configure(command=start)
        self.stop_button.configure(command=stop)
        self.export_button.configure(command=export)
        for variable in (self.tax_id, self.jks_path, self.alias, self.start_year, self.end_year, self.root_path, self.mode):
            variable.trace_add("write", lambda *_: changed())

    def set_aliases(self, labels: list[str]) -> None:
        values = tuple(f"{index}: {label}" for index, label in enumerate(labels))
        self.alias_box.configure(values=values or ("0",))
        self.alias.set(values[0] if values else "0")

    def set_log(self, lines: list[str]) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(lines))
        self.log.see("end")
        self.log.configure(state="disabled")


__all__ = ["MainView"]
