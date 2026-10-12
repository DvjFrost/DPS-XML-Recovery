"""Fail-closed, read-only preflight for the installed IIT EUSignCP library."""

from __future__ import annotations

import ctypes
import json
import os
import struct
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_DLL_CANDIDATES = (
    Path(
        r"C:\Program Files (x86)\Institute of Informational Technologies"
        r"\Certificate Authority-1.3\End User\EUSignCP.dll"
    ),
)

DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
PBOOL = ctypes.POINTER(BOOL)
PDWORD = ctypes.POINTER(DWORD)
PSTR = ctypes.c_char_p

EU_PATH_MAX_LENGTH = 1041
EU_ADDRESS_MAX_LENGTH = 257
EU_PORT_MAX_LENGTH = 6
EU_USER_NAME_MAX_LENGTH = 65
EU_PASS_MAX_LENGTH = 65
EU_COMMON_NAME_MAX_LENGTH = 65


@dataclass(frozen=True)
class GetterSpec:
    label: str
    export: str


GETTER_SPECS = (
    GetterSpec("mode", "EUGetModeSettings"),
    GetterSpec("file_store", "EUGetFileStoreSettings"),
    GetterSpec("proxy", "EUGetProxySettings"),
    GetterSpec("ocsp", "EUGetOCSPSettings"),
    GetterSpec("ocsp_access_mode", "EUGetOCSPAccessInfoModeSettings"),
    GetterSpec("tsp", "EUGetTSPSettings"),
    GetterSpec("ldap", "EUGetLDAPSettings"),
    GetterSpec("cmp", "EUGetCMPSettings"),
    GetterSpec("log", "EUGetLogSettings"),
    GetterSpec("tsl", "EUGetTSLSettings"),
)

REQUIRED_EXPORTS = (
    "EUInitialize",
    "EUIsInitialized",
    "EUDoesNeedSetSettings",
    "EUFinalize",
    *(spec.export for spec in GETTER_SPECS),
)


class IITPreflightError(RuntimeError):
    """Expected preflight failure with a non-sensitive public message."""


class IITNotFoundError(IITPreflightError):
    pass


class IITMultipleCandidatesError(IITPreflightError):
    pass


@dataclass(frozen=True)
class StaticIITReport:
    exists: bool
    architecture: str = "unknown"
    file_version: str | None = None
    product_version: str | None = None
    signature_valid: bool = False
    publisher_trusted: bool = False
    required_exports_present: bool = False
    missing_exports: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return (
            self.exists
            and self.architecture == "x86"
            and self.signature_valid
            and self.publisher_trusted
            and self.required_exports_present
        )

    def to_public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["usable"] = self.usable
        return result


@dataclass(frozen=True)
class SettingIITReport:
    available: bool
    status: int | None = None
    configured: bool = False
    flags: Mapping[str, bool | int] = field(default_factory=dict)
    error: bool = False


@dataclass(frozen=True)
class RuntimeIITReport:
    static: StaticIITReport
    initialize_status: int | None = None
    initialized: bool = False
    needs_settings: bool | None = None
    settings: Mapping[str, SettingIITReport] = field(default_factory=dict)
    finalized: bool = False
    runtime_error: bool = False

    @property
    def success(self) -> bool:
        return (
            self.static.usable
            and self.initialize_status == 0
            and self.initialized
            and self.needs_settings is False
            and not self.runtime_error
            and self.finalized
            and all(not setting.error and setting.status == 0 for setting in self.settings.values())
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "static": self.static.to_public_dict(),
            "initialize_status": self.initialize_status,
            "initialized": self.initialized,
            "needs_settings": self.needs_settings,
            "settings": {name: asdict(value) for name, value in self.settings.items()},
            "finalized": self.finalized,
            "runtime_error": self.runtime_error,
            "success": self.success,
            "safety": {
                "read_only_getters_only": True,
                "setters_called": False,
                "private_key_api_called": False,
                "signing_api_called": False,
                "sensitive_text_values_emitted": False,
            },
        }


def locate_iit_dll(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        candidate = Path(explicit_path).expanduser().resolve()
        if not candidate.is_file():
            raise IITNotFoundError("Указанная библиотека ІІТ не найдена")
        return candidate
    found = tuple(path.resolve() for path in DEFAULT_DLL_CANDIDATES if path.is_file())
    if not found:
        raise IITNotFoundError("Библиотека EUSignCP.dll не найдена")
    if len(found) != 1:
        raise IITMultipleCandidatesError("Найдено несколько библиотек ІІТ; требуется явный выбор")
    return found[0]


def _pe_architecture(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                return "unknown"
            stream.seek(0x3C)
            offset_data = stream.read(4)
            if len(offset_data) != 4:
                return "unknown"
            stream.seek(struct.unpack("<I", offset_data)[0])
            if stream.read(4) != b"PE\0\0":
                return "unknown"
            machine_data = stream.read(2)
            if len(machine_data) != 2:
                return "unknown"
            machine = struct.unpack("<H", machine_data)[0]
    except OSError:
        return "unknown"
    return {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(machine, "unknown")


class _VSFixedFileInfo(ctypes.Structure):
    _fields_ = [(name, DWORD) for name in (
        "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
        "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags",
        "dwFileOS", "dwFileType", "dwFileSubtype", "dwFileDateMS", "dwFileDateLS",
    )]


def _read_versions(path: Path) -> tuple[str | None, str | None]:
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None, None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            return None, None
        pointer, length = ctypes.c_void_p(), ctypes.c_uint()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None, None
        info = ctypes.cast(pointer, ctypes.POINTER(_VSFixedFileInfo)).contents
    except (AttributeError, OSError):
        return None, None

    def render(ms: int, ls: int) -> str:
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"

    return render(info.dwFileVersionMS, info.dwFileVersionLS), render(
        info.dwProductVersionMS, info.dwProductVersionLS
    )


def _verify_authenticode(path: Path) -> tuple[bool, bool]:
    script = (
        "Import-Module $PSHOME\\Modules\\Microsoft.PowerShell.Security\\"
        "Microsoft.PowerShell.Security.psd1 -ErrorAction Stop;"
        "$p=[Console]::In.ReadToEnd();"
        "$s=Get-AuthenticodeSignature -LiteralPath $p;"
        "[pscustomobject]@{status=[string]$s.Status;"
        "subject=if($s.SignerCertificate){$s.SignerCertificate.Subject}else{''}}"
        "|ConvertTo-Json -Compress"
    )
    startupinfo = None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        powershell = "powershell.exe"
        if struct.calcsize("P") == 4 and os.environ.get("PROCESSOR_ARCHITEW6432"):
            sysnative = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            if sysnative.is_file():
                powershell = str(sysnative)
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            input=str(path),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 and not result.stderr else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False, False
    subject = str(payload.get("subject", ""))
    signature_valid = payload.get("status") == "Valid"
    normalized = subject.replace('"', "").upper()
    publisher_trusted = (
        signature_valid
        and "AT IIT" in normalized
        and "SERIALNUMBER=22723472" in normalized
        and "C=UA" in normalized
    )
    return signature_valid, publisher_trusted


def _probe_exports(path: Path) -> tuple[str, ...]:
    if struct.calcsize("P") != 4:
        return REQUIRED_EXPORTS
    library = ctypes.WinDLL(str(path), use_last_error=True)
    try:
        return tuple(name for name in REQUIRED_EXPORTS if not hasattr(library, name))
    finally:
        handle = library._handle
        del library
        ctypes.windll.kernel32.FreeLibrary(handle)


def inspect_iit_dll(path: Path) -> StaticIITReport:
    path = Path(path)
    if not path.is_file():
        return StaticIITReport(exists=False)
    architecture = _pe_architecture(path)
    file_version, product_version = _read_versions(path)
    if architecture != "x86":
        return StaticIITReport(
            exists=True,
            architecture=architecture,
            file_version=file_version,
            product_version=product_version,
        )
    signature_valid, publisher_trusted = _verify_authenticode(path)
    if not signature_valid or not publisher_trusted:
        return StaticIITReport(
            exists=True,
            architecture=architecture,
            file_version=file_version,
            product_version=product_version,
            signature_valid=signature_valid,
            publisher_trusted=publisher_trusted,
        )
    try:
        missing = _probe_exports(path)
    except (AttributeError, OSError):
        missing = REQUIRED_EXPORTS
    return StaticIITReport(
        exists=True,
        architecture=architecture,
        file_version=file_version,
        product_version=product_version,
        signature_valid=True,
        publisher_trusted=True,
        required_exports_present=not missing,
        missing_exports=tuple(missing),
    )


def _buffer(size: int) -> ctypes.Array:
    return ctypes.create_string_buffer(size)


def _wipe(*buffers: ctypes.Array) -> None:
    for buffer in buffers:
        ctypes.memset(ctypes.addressof(buffer), 0, ctypes.sizeof(buffer))


def _present(buffer: ctypes.Array) -> bool:
    return bool(buffer.value)


class _RealBackend:
    def __init__(self, path: Path) -> None:
        if struct.calcsize("P") != 4:
            raise OSError("incompatible process architecture")
        self._dll = ctypes.WinDLL(str(path), use_last_error=True)
        self._bind("EUInitialize", [], DWORD)
        self._bind("EUIsInitialized", [], BOOL)
        self._bind("EUDoesNeedSetSettings", [], BOOL)
        self._bind("EUFinalize", [], None)

    def _bind(self, name: str, argtypes: list[Any], restype: Any) -> Any:
        function = getattr(self._dll, name)
        function.argtypes = argtypes
        function.restype = restype
        return function

    def initialize(self) -> int:
        return int(self._dll.EUInitialize())

    def is_initialized(self) -> bool:
        return bool(self._dll.EUIsInitialized())

    def does_need_set_settings(self) -> bool:
        return bool(self._dll.EUDoesNeedSetSettings())

    def finalize(self) -> None:
        self._dll.EUFinalize()

    def _call(self, name: str, argtypes: list[Any], args: list[Any]) -> int:
        return int(self._bind(name, argtypes, DWORD)(*args))

    def get_setting(self, name: str) -> tuple[int, dict[str, bool | int]]:
        return getattr(self, f"_get_{name}")()

    def _get_EUGetModeSettings(self):
        offline = BOOL()
        return self._call("EUGetModeSettings", [PBOOL], [ctypes.byref(offline)]), {"offline": bool(offline.value)}

    def _get_EUGetFileStoreSettings(self):
        path, values, expiry = _buffer(EU_PATH_MAX_LENGTH), [BOOL() for _ in range(6)], DWORD()
        try:
            status = self._call("EUGetFileStoreSettings", [PSTR] + [PBOOL] * 6 + [PDWORD], [path] + [ctypes.byref(v) for v in values] + [ctypes.byref(expiry)])
            return status, {"path_present": _present(path), "check_crls": bool(values[0].value), "auto_refresh": bool(values[1].value), "own_crls_only": bool(values[2].value), "full_and_delta_crls": bool(values[3].value), "auto_download_crls": bool(values[4].value), "save_loaded_certs": bool(values[5].value), "expire_time": int(expiry.value)}
        finally:
            _wipe(path)

    def _get_EUGetProxySettings(self):
        use_proxy, anonymous, save_password = BOOL(), BOOL(), BOOL()
        address, port, user, password = _buffer(EU_ADDRESS_MAX_LENGTH), _buffer(EU_PORT_MAX_LENGTH), _buffer(EU_USER_NAME_MAX_LENGTH), _buffer(EU_PASS_MAX_LENGTH)
        try:
            status = self._call("EUGetProxySettings", [PBOOL, PBOOL, PSTR, PSTR, PSTR, PSTR, PBOOL], [ctypes.byref(use_proxy), ctypes.byref(anonymous), address, port, user, password, ctypes.byref(save_password)])
            return status, {"use_proxy": bool(use_proxy.value), "anonymous": bool(anonymous.value), "endpoint_present": _present(address) or _present(port), "credentials_present": _present(user) or _present(password), "save_password": bool(save_password.value)}
        finally:
            _wipe(address, port, user, password)

    def _endpoint(self, name: str, flag_names: list[str]):
        flags, address, port = [BOOL() for _ in flag_names], _buffer(EU_ADDRESS_MAX_LENGTH), _buffer(EU_PORT_MAX_LENGTH)
        try:
            status = self._call(name, [PBOOL] * len(flags) + [PSTR, PSTR], [ctypes.byref(v) for v in flags] + [address, port])
            values = {key: bool(value.value) for key, value in zip(flag_names, flags)}
            values["endpoint_present"] = _present(address) or _present(port)
            return status, values
        finally:
            _wipe(address, port)

    def _get_EUGetOCSPSettings(self): return self._endpoint("EUGetOCSPSettings", ["use_ocsp", "before_store"])
    def _get_EUGetTSPSettings(self): return self._endpoint("EUGetTSPSettings", ["get_stamps"])

    def _get_EUGetOCSPAccessInfoModeSettings(self):
        enabled = BOOL()
        return self._call("EUGetOCSPAccessInfoModeSettings", [PBOOL], [ctypes.byref(enabled)]), {"enabled": bool(enabled.value)}

    def _get_EUGetLDAPSettings(self):
        use_ldap, anonymous = BOOL(), BOOL()
        address, port, user, password = _buffer(EU_ADDRESS_MAX_LENGTH), _buffer(EU_PORT_MAX_LENGTH), _buffer(EU_USER_NAME_MAX_LENGTH), _buffer(EU_PASS_MAX_LENGTH)
        try:
            status = self._call("EUGetLDAPSettings", [PBOOL, PSTR, PSTR, PBOOL, PSTR, PSTR], [ctypes.byref(use_ldap), address, port, ctypes.byref(anonymous), user, password])
            return status, {"use_ldap": bool(use_ldap.value), "anonymous": bool(anonymous.value), "endpoint_present": _present(address) or _present(port), "credentials_present": _present(user) or _present(password)}
        finally:
            _wipe(address, port, user, password)

    def _get_EUGetCMPSettings(self):
        use_cmp, address, port, common_name = BOOL(), _buffer(EU_ADDRESS_MAX_LENGTH), _buffer(EU_PORT_MAX_LENGTH), _buffer(EU_COMMON_NAME_MAX_LENGTH)
        try:
            status = self._call("EUGetCMPSettings", [PBOOL, PSTR, PSTR, PSTR], [ctypes.byref(use_cmp), address, port, common_name])
            return status, {"use_cmp": bool(use_cmp.value), "endpoint_present": _present(address) or _present(port), "common_name_present": _present(common_name)}
        finally:
            _wipe(address, port, common_name)

    def _get_EUGetLogSettings(self):
        system, use_agent, only_errors = BOOL(), BOOL(), BOOL()
        address, port = _buffer(EU_ADDRESS_MAX_LENGTH), _buffer(EU_PORT_MAX_LENGTH)
        try:
            status = self._call("EUGetLogSettings", [PBOOL, PBOOL, PSTR, PSTR, PBOOL], [ctypes.byref(system), ctypes.byref(use_agent), address, port, ctypes.byref(only_errors)])
            return status, {"system": bool(system.value), "use_report_agent": bool(use_agent.value), "endpoint_present": _present(address) or _present(port), "only_errors": bool(only_errors.value)}
        finally:
            _wipe(address, port)

    def _get_EUGetTSLSettings(self):
        use_tsl, auto_download, address = BOOL(), BOOL(), _buffer(EU_PATH_MAX_LENGTH)
        try:
            status = self._call("EUGetTSLSettings", [PBOOL, PBOOL, PSTR], [ctypes.byref(use_tsl), ctypes.byref(auto_download), address])
            return status, {"use_tsl": bool(use_tsl.value), "auto_download_tsl": bool(auto_download.value), "address_present": _present(address)}
        finally:
            _wipe(address)


def _safe_flags(values: Mapping[str, Any]) -> dict[str, bool | int]:
    return {key: value for key, value in values.items() if isinstance(value, (bool, int)) and not isinstance(value, str)}


def run_iit_preflight(
    path: Path,
    *,
    backend_factory: Callable[[Path], Any] = _RealBackend,
) -> RuntimeIITReport:
    static = inspect_iit_dll(path)
    if not static.usable:
        return RuntimeIITReport(static=static)
    try:
        backend = backend_factory(Path(path))
        initialize_status = int(backend.initialize())
    except Exception:
        return RuntimeIITReport(static=static, runtime_error=True)
    if initialize_status != 0:
        return RuntimeIITReport(static=static, initialize_status=initialize_status)

    initialized = False
    needs_settings: bool | None = None
    settings: dict[str, SettingIITReport] = {}
    runtime_error = False
    finalized = False
    try:
        initialized = bool(backend.is_initialized())
        needs_settings = bool(backend.does_need_set_settings())
        for spec in GETTER_SPECS:
            try:
                status, values = backend.get_setting(spec.export)
                flags = _safe_flags(values) if status == 0 else {}
                settings[spec.label] = SettingIITReport(
                    available=True,
                    status=int(status),
                    configured=status == 0 and any(bool(value) for value in flags.values()),
                    flags=flags,
                    error=status != 0,
                )
                runtime_error = runtime_error or status != 0
            except Exception:
                settings[spec.label] = SettingIITReport(available=True, error=True)
                runtime_error = True
    except Exception:
        runtime_error = True
    finally:
        try:
            backend.finalize()
            finalized = True
        except Exception:
            runtime_error = True
    return RuntimeIITReport(
        static=static,
        initialize_status=initialize_status,
        initialized=initialized,
        needs_settings=needs_settings,
        settings=settings,
        finalized=finalized,
        runtime_error=runtime_error,
    )
