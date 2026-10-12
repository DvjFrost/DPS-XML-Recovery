# DPS XML Recovery 2.0.2 — acceptance checklist

Date: 2026-10-12. Branch: `codex/portable-v2`.

## Release gate

- [x] Public artifact built with CPython 3.12.10 x86 and PyInstaller 6.22.3 in `onedir` mode.
- [x] Public executable PE architecture is x86.
- [x] Full, inventory-only, and audit-only modes exercised through a frozen offline worker.
- [x] Cooperative stop creates a durable checkpoint; the next run resumes from the following item.
- [x] Wrong key password, authorization failure, corrupt state, invalid/partial XML, and worker crash never produce PASS.
- [x] Unicode paths (Cyrillic, Ukrainian letters, spaces, and supplementary Unicode) survive JSONL IPC unchanged.
- [x] Inventory and download progress preserve phase order and bounded `current/total` values.
- [x] The public frozen worker completed a safe `audit-only` run without IIT or DPS network access.
- [x] The public GUI opened from a copied Unicode path and closed cleanly through `WM_CLOSE`.
- [x] Real Tk checks confirmed keyboard bindings (`Ctrl+O`, `Ctrl+R`, `Enter`, `Escape`) and visible action buttons at 760x600 with 150% Tk scaling.
- [x] Package policy found no JKS, XML, SQLite state, `.part`, logs, IIT DLLs, or secret-pattern matches.
- [x] ZIP contents match the unpacked release byte-for-byte.
- [x] Public PyInstaller archive does not contain the acceptance launcher/runtime.
- [x] Full automated suite passed: 177 tests after release-documentation guardrails.
- [x] User-confirmed real 2.0.2 run: 135 primary XML, 212 related XML, 0 invalid XML, 0 `.part`, 0 audit errors, final audit PASS; Unicode paths and progress confirmed.
- [ ] Windows 11 acceptance — unavailable, not tested.
- [ ] Windows Server 2016 acceptance — unavailable, not tested.
- [ ] GitHub Release — intentionally not created; separate approval required.

## Reproducible commands

```powershell
scripts\build-acceptance.ps1 -Python <path-to-python-3.12-x86>\python.exe
$env:DPS_ACCEPTANCE_EXE = (Resolve-Path build\acceptance-dist\DPSXmlRecoveryAcceptance\DPSXmlRecoveryAcceptance.exe).Path
$env:DPS_PUBLIC_EXE = (Resolve-Path dist\DPS-XML-Recovery-2.0.2-win-x86\DPSXmlRecovery.exe).Path
& <path-to-python-3.12-x86>\python.exe -m pytest -q
```

The acceptance executable is an internal test artifact. Its launcher always injects the deterministic offline runner. It is not copied into the public ZIP.
