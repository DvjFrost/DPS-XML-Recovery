# Portable x86 release build

Build on Windows with official CPython 3.12 x86. Pass its interpreter explicitly; the script rejects any non-3.12 or non-32-bit interpreter before invoking PyInstaller.

```powershell
.\scripts\build-portable.ps1 -Python '<path-to-python-3.12-x86>\python.exe' -Version '2.0.2'
```

The dependency lock uses pinned versions and SHA-256 hashes. The script builds an onedir application with UPX disabled, adds only release documentation and an empty `portable-data`, creates the ZIP, compares every ZIP member hash with the release directory, checks the executable PE architecture, scans for forbidden user data and secret-like text, and writes `SHA256SUMS.txt` plus `package-verification.json`.

Release outputs under `dist` are intentionally ignored by Git. Never add IIT libraries, key material, recovered XML, recovery state, manifests, audit output or operational logs to a release directory.
