"""Crash-safe SQLite resume state and portable JSON/CSV exports."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class RecoveryState(AbstractContextManager["RecoveryState"]):
    """SQLite is a cache; exports and per-file metadata remain authoritative."""

    def __init__(self, db_path: Path | str, data_root: Path | str):
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS periods (
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                next_page INTEGER NOT NULL DEFAULT 0,
                total_pages INTEGER,
                total_elements INTEGER,
                complete INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (year, month)
            );
            CREATE TABLE IF NOT EXISTS documents (
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                cod_regdoc INTEGER NOT NULL,
                form_code TEXT,
                form_name TEXT,
                status_text TEXT,
                occurrences INTEGER NOT NULL DEFAULT 1,
                api_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (year, cod_regdoc)
            );
            CREATE TABLE IF NOT EXISTS inventory_pages (
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                page INTEGER NOT NULL,
                records_received INTEGER NOT NULL,
                total_elements INTEGER,
                total_pages INTEGER NOT NULL,
                cod_regdocs_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (year, month, page)
            );
            CREATE TABLE IF NOT EXISTS objects (
                object_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER,
                document_id INTEGER NOT NULL,
                parent_id INTEGER,
                form_code TEXT,
                form_name TEXT,
                status TEXT NOT NULL,
                relative_path TEXT,
                size_bytes INTEGER,
                sha256 TEXT,
                xml_valid INTEGER,
                content_type TEXT,
                downloaded_at TEXT,
                source_endpoint TEXT,
                error TEXT,
                api_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                operation TEXT NOT NULL,
                document_id INTEGER,
                message TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def checkpoint_page(
        self,
        year: int,
        month: int,
        page: int,
        total_pages: int,
        records: Iterable[dict[str, Any]],
        total_elements: int | None = None,
    ) -> None:
        now = _utc_now()
        records = list(records)
        with self.connection:
            for record in records:
                document_id = int(record["codRegdoc"])
                record_year = int(record.get("periodYear") or year)
                record_month = int(record.get("periodMonth") or month)
                self.connection.execute(
                    """
                    INSERT INTO documents
                        (year, month, cod_regdoc, form_code, form_name, status_text,
                         occurrences, api_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(year, cod_regdoc) DO UPDATE SET
                        month=excluded.month,
                        form_code=excluded.form_code,
                        form_name=excluded.form_name,
                        status_text=excluded.status_text,
                        occurrences=documents.occurrences + 1,
                        api_json=excluded.api_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        record_year,
                        record_month,
                        document_id,
                        record.get("doc") or record.get("cDoc"),
                        record.get("docName"),
                        record.get("flagName"),
                        json.dumps(record, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
            self.connection.execute(
                """
                INSERT INTO inventory_pages
                    (year, month, page, records_received, total_elements, total_pages,
                     cod_regdocs_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(year, month, page) DO UPDATE SET
                    records_received=excluded.records_received,
                    total_elements=excluded.total_elements,
                    total_pages=excluded.total_pages,
                    cod_regdocs_json=excluded.cod_regdocs_json,
                    fetched_at=excluded.fetched_at
                """,
                (
                    year, month, page, len(records), total_elements, total_pages,
                    json.dumps([int(record["codRegdoc"]) for record in records]), now,
                ),
            )
            next_page = page + 1
            complete = int(total_pages == 0 or next_page >= total_pages)
            self.connection.execute(
                """
                INSERT INTO periods
                    (year, month, next_page, total_pages, total_elements, complete, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(year, month) DO UPDATE SET
                    next_page=excluded.next_page,
                    total_pages=excluded.total_pages,
                    total_elements=excluded.total_elements,
                    complete=excluded.complete,
                    error=NULL,
                    updated_at=excluded.updated_at
                """,
                (year, month, next_page, total_pages, total_elements, complete, now),
            )

    def import_document_record(
        self, record: dict[str, Any], *, fallback_year: int, fallback_month: int
    ) -> None:
        """Restore inventory identity from portable primary metadata without inflating occurrences."""
        document_id = int(record["codRegdoc"])
        year = int(record.get("periodYear") or fallback_year)
        month = int(record.get("periodMonth") or fallback_month)
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO documents
                    (year, month, cod_regdoc, form_code, form_name, status_text,
                     occurrences, api_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(year, cod_regdoc) DO UPDATE SET
                    month=excluded.month,
                    form_code=excluded.form_code,
                    form_name=excluded.form_name,
                    status_text=excluded.status_text,
                    api_json=excluded.api_json,
                    updated_at=excluded.updated_at
                """,
                (
                    year,
                    month,
                    document_id,
                    record.get("doc") or record.get("cDoc"),
                    record.get("docName"),
                    record.get("flagName"),
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )

    def mark_period_error(self, year: int, month: int, message: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO periods (year, month, updated_at, error)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(year, month) DO UPDATE SET
                    complete=0, error=excluded.error, updated_at=excluded.updated_at
                """,
                (year, month, _utc_now(), message),
            )

    def mark_period_complete(self, year: int, month: int) -> None:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE periods SET complete=1, error=NULL, updated_at=? WHERE year=? AND month=?",
                (_utc_now(), year, month),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"period not found: {year}-{month:02d}")

    def count_documents_for_period(self, year: int, month: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM documents WHERE year=? AND month=?", (year, month)
        ).fetchone()
        return int(row[0])

    def get_period(self, year: int, month: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM periods WHERE year=? AND month=?", (year, month)
        ).fetchone()
        return self._row(row)

    def list_periods(self) -> list[dict[str, Any]]:
        return [self._row(row) for row in self.connection.execute(
            "SELECT * FROM periods ORDER BY year DESC, month DESC"
        )]

    def list_documents(self) -> list[dict[str, Any]]:
        return [self._decode_row(row) for row in self.connection.execute(
            "SELECT * FROM documents ORDER BY year DESC, month DESC, cod_regdoc"
        )]

    def list_inventory_pages(self) -> list[dict[str, Any]]:
        result = []
        for row in self.connection.execute(
            "SELECT * FROM inventory_pages ORDER BY year DESC, month DESC, page"
        ):
            item = dict(row)
            item["cod_regdocs"] = json.loads(item.pop("cod_regdocs_json"))
            result.append(item)
        return result

    def get_document(self, year: int, document_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE year=? AND cod_regdoc=?", (year, document_id)
        ).fetchone()
        return self._decode_row(row) if row else None

    def upsert_object(self, **values: Any) -> None:
        columns = (
            "object_key", "kind", "year", "month", "document_id", "parent_id",
            "form_code", "form_name", "status", "relative_path", "size_bytes",
            "sha256", "xml_valid", "content_type", "downloaded_at",
            "source_endpoint", "error", "api_json", "updated_at",
        )
        row = dict(values)
        row["api_json"] = json.dumps(row.pop("api_record", {}), ensure_ascii=False, sort_keys=True)
        row["xml_valid"] = None if row.get("xml_valid") is None else int(bool(row["xml_valid"]))
        row["updated_at"] = _utc_now()
        params = [row.get(column) for column in columns]
        assignments = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
        column_sql = ", ".join(columns)
        placeholder_sql = ", ".join(["?"] * len(columns))
        with self.connection:
            self.connection.execute(
                f"INSERT INTO objects ({column_sql}) VALUES ({placeholder_sql}) "
                f"ON CONFLICT(object_key) DO UPDATE SET {assignments}",
                params,
            )

    def get_object(self, object_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM objects WHERE object_key=?", (object_key,)
        ).fetchone()
        return self._decode_row(row) if row else None

    def list_objects(self) -> list[dict[str, Any]]:
        return [self._decode_row(row) for row in self.connection.execute(
            "SELECT * FROM objects ORDER BY year DESC, COALESCE(month, 0) DESC, kind, document_id"
        )]

    def record_event(
        self, level: str, operation: str, message: str, document_id: int | None = None
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO events(timestamp, level, operation, document_id, message) VALUES(?,?,?,?,?)",
                (_utc_now(), level, operation, document_id, message),
            )

    def export_all(self) -> None:
        documents = self.list_documents()
        periods = self.list_periods()
        objects = self.list_objects()
        pages = self.list_inventory_pages()
        generated = _utc_now()
        self._write_json(
            self.data_root / "inventory.json",
            {
                "generated_at": generated,
                "periods": periods,
                "pages": pages,
                "documents": documents,
            },
        )
        self._write_csv(self.data_root / "inventory.csv", documents)
        self._write_json(
            self.data_root / "manifest.json",
            {"generated_at": generated, "objects": objects},
        )
        self._write_csv(self.data_root / "manifest.csv", objects)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        fieldnames = sorted({key for row in rows for key in row if key != "api_record"})
        if not fieldnames:
            _atomic_text(path, "", encoding="utf-8-sig")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        if "complete" in result:
            result["complete"] = bool(result["complete"])
        if "xml_valid" in result and result["xml_valid"] is not None:
            result["xml_valid"] = bool(result["xml_valid"])
        return result

    @classmethod
    def _decode_row(cls, row: sqlite3.Row | None) -> dict[str, Any]:
        result = cls._row(row) or {}
        if "api_json" in result:
            result["api_record"] = json.loads(result.pop("api_json") or "{}")
        return result
