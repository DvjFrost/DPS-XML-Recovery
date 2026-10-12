"""Interactive full read-only DPS historical XML recovery."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import tempfile
from getpass import getpass
from pathlib import Path

from dps_api import AuthFatalError, DPSClient, PaginationSafetyError
from dps_archive import ArchiveStore
from dps_audit import audit_archive
from dps_progress import RecoveryProgress
from dps_recovery_engine import RecoveryEngine
from dps_recovery_state import RecoveryState
from iit_eusign_ctypes import EUSignCP
from test_sign import encode_tax_id


DEFAULT_ROOT = Path(os.environ.get("DPS_XML_RECOVERY_ROOT", "recovery"))
LEGACY_ROOT = os.environ.get("DPS_XML_RECOVERY_LEGACY_ROOT")
LOGGER = logging.getLogger("dps_recovery")


def create_verified_authorization(api: EUSignCP, tax_id: str) -> tuple[str, str | None]:
    original = encode_tax_id(tax_id)
    signed = api.sign_data_internal(original, append_certificate=True)
    verified = api.verify_data_internal(signed)
    if not hmac.compare_digest(verified.data, original):
        raise RuntimeError("STOP: recovered signed bytes differ from input")
    if not verified.certificate_present:
        raise RuntimeError("STOP: signer certificate is absent from signature")
    return signed.decode("ascii", errors="strict"), verified.signer_common_name


def setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def _open_state(data_root: Path, force_reindex: bool) -> RecoveryState:
    db_path = data_root / "state.sqlite3"
    try:
        state = RecoveryState(db_path, data_root)
        check = state.connection.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError("quick_check failed")
        return state
    except sqlite3.DatabaseError as error:
        try:
            state.close()
        except (UnboundLocalError, Exception):
            pass
        if not force_reindex:
            raise RuntimeError("state.sqlite3 is corrupt; rerun with --reindex") from error
        if db_path.exists():
            backup = db_path.with_suffix(".sqlite3.unreadable")
            counter = 1
            while backup.exists():
                backup = db_path.with_suffix(f".sqlite3.unreadable.{counter}")
                counter += 1
            db_path.replace(backup)
        return RecoveryState(db_path, data_root)


def _atomic_audit_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".audit-", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _print_audit(report, data_root: Path) -> None:
    print("=" * 60)
    print("DPS XML RECOVERY " + ("COMPLETE" if report.clean else "INCOMPLETE"))
    print("=" * 60)
    print(f"Inventory documents: {report.inventory_documents}")
    print(f"Primary XML valid:  {report.primary_valid}")
    print(f"Related XML valid:  {report.related_valid}")
    print(f"Invalid files:      {report.invalid_files}")
    print(f"Unfinished .part:   {report.part_files}")
    print(f"Errors:             {len(report.errors)}")
    print(f"Manifest:           {data_root / 'manifest.json'}")
    print(f"Archive:            {data_root / 'archive'}")
    if report.errors:
        print("Unresolved items:")
        for item in report.errors[:100]:
            print(f"  - {item}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--start-year", type=int, default=2026)
    parser.add_argument("--end-year", type=int, default=2010)
    parser.add_argument("--probe-year", type=int, default=2026)
    parser.add_argument("--probe-month", type=int, default=7)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.end_year < 2010:
        raise ValueError("end-year must be 2010 or later")
    if LEGACY_ROOT and args.root.resolve() == Path(LEGACY_ROOT).resolve():
        raise ValueError("Старый контрольный архив запрещён как --root; используйте независимый новый каталог")
    data_root = args.root / "data"
    setup_logging(args.root / "logs" / "download.log")

    with _open_state(data_root, args.reindex) as state:
        archive = ArchiveStore(data_root, state)
        if args.reindex:
            stats = archive.reindex()
            state.export_all()
            print(f"Reindex complete: valid={stats['valid']} invalid={stats['invalid']}")
        if args.audit_only:
            report = audit_archive(state, archive)
            _atomic_audit_report(data_root / "audit-report.json", report.to_dict())
            _print_audit(report, data_root)
            return 0 if report.clean else 2

        removed_parts = archive.cleanup_part_files()
        if removed_parts:
            LOGGER.warning("removed %d unfinished .part files", removed_parts)

        tax_id = input("EDRPOU/RNOKPP: ")
        raw_path = input("JKS path (local): ")
        jks_path = Path(raw_path)
        if not jks_path.is_file():
            raise FileNotFoundError("The selected JKS file does not exist")
        password = getpass("JKS/private-key password: ")
        authorization = ""
        try:
            with EUSignCP() as api:
                aliases = api.list_jks_aliases(jks_path)
                if not aliases:
                    raise RuntimeError("No private-key aliases found in JKS")
                for index, alias in enumerate(aliases, start=1):
                    print(f"  {index}: {alias.decode('cp1251', errors='replace')}")
                selected = int(input("Select alias number: "))
                if selected < 1 or selected > len(aliases):
                    raise ValueError("Alias number is outside the displayed range")
                api.read_jks_key(jks_path, aliases[selected - 1], password)
                authorization, signer = create_verified_authorization(api, tax_id)
                print("Local IIT signature verification passed")
                print(f"Signer CN: {signer or 'not available'}")
                print(f"Authorization prefix: {authorization[:4]}")
                print(f"Authorization length: {len(authorization)}")
                digest = hashlib.sha256(authorization.encode("ascii")).hexdigest()
                print(f"Authorization SHA-256: {digest}")

                def api_event(level: str, message: str) -> None:
                    getattr(LOGGER, level.lower(), LOGGER.info)(message)

                client = DPSClient(
                    authorization,
                    request_delay=args.delay,
                    event_callback=api_event,
                )
                try:
                    print("Testing the DPS inventory contract with official first-page parameters...")
                    proof = client.probe_pagination(args.probe_year, args.probe_month)
                    print(
                        "Pagination proven: "
                        f"{proof['year']}-{proof['month']:02d}, "
                        f"totalElements={proof['total_elements']}, totalPages={proof['total_pages']}"
                    )
                    LOGGER.info("pagination proven %s", json.dumps(proof, sort_keys=True))
                    if args.probe_only:
                        print("STOP: pagination probe succeeded; mass recovery was not started")
                        return 0

                    progress = RecoveryProgress()
                    try:
                        engine = RecoveryEngine(
                            client,
                            state,
                            archive,
                            start_year=args.start_year,
                            end_year=args.end_year,
                            progress=progress,
                        )
                        inventory_stats = engine.run_inventory()
                        LOGGER.info("inventory complete %s", json.dumps(inventory_stats, sort_keys=True))
                        if inventory_stats["errors"]:
                            print(
                                f"STOP: inventory содержит ошибок: {inventory_stats['errors']}; "
                                "скачивание XML не начато"
                            )
                            return 2
                        if args.inventory_only:
                            print("STOP: полный inventory завершён; скачивание XML не запускалось")
                            return 0
                        download_stats = engine.run_download()
                        LOGGER.info("download complete %s", json.dumps(download_stats, sort_keys=True))
                    finally:
                        progress.close()
                    state.export_all()
                    report = audit_archive(state, archive)
                    _atomic_audit_report(data_root / "audit-report.json", report.to_dict())
                    _print_audit(report, data_root)
                    return 0 if report.clean else 2
                finally:
                    client.close()
        except (PaginationSafetyError, AuthFatalError) as error:
            print(f"STOP: {error}")
            LOGGER.critical("STOP: %s", error)
            return 3
        finally:
            authorization = ""
            password = ""
            raw_path = ""
            tax_id = ""


if __name__ == "__main__":
    raise SystemExit(main())
