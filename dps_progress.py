"""Single-line live status with optional Rich rendering."""

from __future__ import annotations

import time
from typing import Any


class RecoveryProgress:
    def __init__(self):
        self.started = time.monotonic()
        self._live = None
        try:
            from rich.live import Live
            from rich.text import Text

            self._Text = Text
            self._live = Live(Text("Starting DPS recovery..."), refresh_per_second=4)
            self._live.start()
        except ImportError:
            self._Text = None

    def __call__(self, snapshot: dict[str, Any]) -> None:
        text = self.format(snapshot)
        if self._live is not None:
            self._live.update(self._Text(text))
        else:
            print("\r" + text[:150], end="", flush=True)

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
        else:
            print()

    def format(self, snapshot: dict[str, Any]) -> str:
        if snapshot["phase"] == "inventory":
            return (
                "PHASE 1/2 INVENTORY | "
                f"{snapshot['year']:04d}-{snapshot['month']:02d} | "
                f"page {snapshot['page']}/{snapshot['total_pages']} | "
                f"records {snapshot['records_seen']} | unique {snapshot['unique_documents']} | "
                f"errors {snapshot['errors']}"
            )
        total = max(1, int(snapshot["primary_total"]))
        current = int(snapshot["primary_processed"])
        ratio = min(1.0, current / total)
        filled = int(ratio * 20)
        bar = "█" * filled + "░" * (20 - filled)
        elapsed = max(0.001, time.monotonic() - self.started)
        rate = current / elapsed
        eta = (total - current) / rate if rate else 0
        return (
            f"PHASE 2/2 DOWNLOAD [{bar}] {ratio * 100:5.1f}% | "
            f"{current}/{total} left {total-current} | "
            f"{snapshot['current_year']:04d}-{snapshot['current_month']:02d} "
            f"{snapshot.get('current_form') or '-'} id={snapshot['current_id']} | "
            f"XML new/existing/errors {snapshot['primary_downloaded']}/"
            f"{snapshot['primary_existing']}/{snapshot['primary_errors']} | "
            f"related {snapshot['related_downloaded']}/{snapshot['related_found']} "
            f"errors {snapshot['related_errors']} | {rate:.2f} doc/s ETA {eta:.0f}s"
        )

