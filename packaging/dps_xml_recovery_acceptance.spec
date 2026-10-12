# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_root = Path(SPECPATH).parent

a = Analysis(
    [str(project_root / "packaging" / "acceptance_launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "dps_gui.acceptance_runtime",
        "dps_gui.worker_main",
        "dps_gui.protocol",
        "dps_gui.path_contract",
    ],
    excludes=[
        "pytest", "tkinter", "certifi", "iit_eusign_ctypes", "dps_api",
        "dps_archive", "dps_audit", "dps_recovery", "dps_recovery_engine",
        "dps_recovery_state", "test_sign",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DPSXmlRecoveryAcceptance",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DPSXmlRecoveryAcceptance",
)
