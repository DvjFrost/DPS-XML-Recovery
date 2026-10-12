import subprocess
import sys


def test_importing_gui_does_not_import_crypto_binding_or_preflight():
    code = (
        "import sys; import dps_gui.app; "
        "assert 'iit_eusign_ctypes' not in sys.modules; "
        "assert 'dps_gui.iit_preflight' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
