# Task 8 acceptance report — DPS XML Recovery 2.0.2

## Result

Release candidate status: **READY WITH PLATFORM LIMITATIONS**.

The Windows 10 build and all automated acceptance gates passed. The user also confirmed a successful real integration run. Windows 11 and Windows Server 2016 remain unverified and must not be represented as PASS.

## Automated evidence

| Area | Result | Evidence |
|---|---:|---|
| Frozen mock E2E | PASS | Full, inventory-only, audit-only, stop/resume, credential failure, authorization failure, corrupt state, partial XML, crash recovery, Unicode paths, ordered progress |
| Public frozen worker | PASS | Safe local `audit-only`; terminal `audit_pass`, no credentials or DPS network |
| Public frozen GUI | PASS | Opened from copied Unicode path and exited cleanly via normal window close |
| Keyboard/layout | PASS on current Windows 10 host | Real Tk window: four shortcuts bound; action buttons visible at 760x600 and 150% scaling |
| Regression suite | PASS | 177 passed, 0 failed, 0 skipped after release-documentation guardrails |
| Package policy | PASS | x86; 973 files; no missing, forbidden, or secret-hit files; ZIP matches directory |
| Acceptance isolation | PASS | Acceptance modules absent from public PyInstaller archive |

No real key, IIT cryptographic operation, or DPS network request was used during automated acceptance.

## Manual evidence supplied by the user

- Primary XML: 135
- Related XML: 212
- Invalid XML: 0
- Incomplete `.part`: 0
- Audit errors: 0
- Final audit: PASS
- Unicode paths: confirmed
- Progress display: confirmed

This manual result is recorded as user-confirmed external evidence; it was not repeated by the automated suite.

## Defect found and corrected during Task 8

The first acceptance build failed before `worker_ready`: the cancellation token lived in `worker_runtime`, whose import graph eagerly loaded production recovery modules. `StopToken` and `UserRequestedStop` were moved to dependency-free `dps_gui.stop_control`; `worker_main` now lazily imports the production runner only when no injected runner is supplied. Existing downloader/recovery modules and cryptographic binding were not changed.

## Artifact

- ZIP: `DPS-XML-Recovery-2.0.2-win-x86.zip`
- Size: 11,434,957 bytes (10.91 MiB)
- SHA-256: `515d2f2d73681eb600831b11d017ce13fc9ae1d57bf2d3f827954495289ab323`
- Package: x86 `onedir`, 973 files, UPX disabled

## Remaining limitations

1. Windows 11 was not available for execution testing.
2. Windows Server 2016 was not available for execution testing.
3. The build is not code-signed; Windows reputation warnings remain possible.
4. Public operation still depends on an installed, compatible, trusted IIT x86 environment.
5. GitHub Release publication is out of scope until separately authorized.
