"""Small PE/ABI inspection helpers; no cryptographic operations live here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Machine = Literal["x86", "x64", "unknown"]


def read_pe_machine(path: Path) -> Machine:
    """Return the processor architecture recorded in a PE file header."""
    data = path.read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError(f"Not a valid PE file: {path}")
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if pe_offset < 0 or pe_offset + 6 > len(data):
        raise ValueError(f"Invalid PE header offset: {path}")
    if data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        raise ValueError(f"Missing PE signature: {path}")
    machine = int.from_bytes(data[pe_offset + 4 : pe_offset + 6], "little")
    return {0x014C: "x86", 0x8664: "x64"}.get(machine, "unknown")


@dataclass(frozen=True)
class RuntimeReport:
    python_machine: Machine
    pyd_machine: Machine
    dll_machine: Machine
    import_ok: bool
    dll_version: str

    @property
    def compatible(self) -> bool:
        return self.reason == "compatible"

    @property
    def reason(self) -> str:
        architectures = {self.python_machine, self.pyd_machine, self.dll_machine}
        if "unknown" in architectures or len(architectures) != 1:
            return "architecture mismatch or unknown architecture"
        if not self.import_ok:
            return "official Python wrapper import failed"
        return "compatible"
