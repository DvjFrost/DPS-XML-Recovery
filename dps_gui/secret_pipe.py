"""One-shot Windows anonymous pipe and short-lived worker credential session."""

from __future__ import annotations

import ctypes
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .model import RecoveryRequest


HANDLE = ctypes.c_void_p
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
LPDWORD = ctypes.POINTER(DWORD)
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
HANDLE_FLAG_INHERIT = 0x00000001
MAX_SECRET_BYTES = 1024 * 1024


class _SecurityAttributes(ctypes.Structure):
    _fields_ = (
        ("nLength", DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", BOOL),
    )


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreatePipe.argtypes = (
    ctypes.POINTER(HANDLE),
    ctypes.POINTER(HANDLE),
    ctypes.POINTER(_SecurityAttributes),
    DWORD,
)
_kernel32.CreatePipe.restype = BOOL
_kernel32.SetHandleInformation.argtypes = (HANDLE, DWORD, DWORD)
_kernel32.SetHandleInformation.restype = BOOL
_kernel32.WriteFile.argtypes = (HANDLE, ctypes.c_void_p, DWORD, LPDWORD, ctypes.c_void_p)
_kernel32.WriteFile.restype = BOOL
_kernel32.ReadFile.argtypes = (HANDLE, ctypes.c_void_p, DWORD, LPDWORD, ctypes.c_void_p)
_kernel32.ReadFile.restype = BOOL
_kernel32.CloseHandle.argtypes = (HANDLE,)
_kernel32.CloseHandle.restype = BOOL


class SecretPipeError(RuntimeError):
    pass


class CredentialSessionError(RuntimeError):
    pass


def wipe_bytearray(value: bytearray) -> None:
    if value:
        ctypes.memset((ctypes.c_ubyte * len(value)).from_buffer(value), 0, len(value))


def _close_handle(handle: int) -> None:
    if handle and handle != INVALID_HANDLE_VALUE:
        _kernel32.CloseHandle(HANDLE(handle))


def _write_all(handle: int, data: bytearray) -> None:
    offset = 0
    while offset < len(data):
        written = DWORD()
        view = (ctypes.c_ubyte * (len(data) - offset)).from_buffer(data, offset)
        if not _kernel32.WriteFile(
            HANDLE(handle), view, len(view), ctypes.byref(written), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not written.value:
            raise SecretPipeError("secret pipe write made no progress")
        offset += int(written.value)


def _read_exact(handle: int, size: int) -> bytearray:
    result = bytearray(size)
    offset = 0
    try:
        while offset < size:
            received = DWORD()
            view = (ctypes.c_ubyte * (size - offset)).from_buffer(result, offset)
            if not _kernel32.ReadFile(
                HANDLE(handle), view, len(view), ctypes.byref(received), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not received.value:
                raise SecretPipeError("secret pipe closed before payload completed")
            offset += int(received.value)
        return result
    except BaseException:
        wipe_bytearray(result)
        raise


class SecretPipe:
    def __init__(self, read_handle: int, write_handle: int) -> None:
        self.read_handle = read_handle
        self.write_handle = write_handle

    @classmethod
    def create(cls) -> "SecretPipe":
        read_handle, write_handle = HANDLE(), HANDLE()
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes),
            None,
            True,
        )
        if not _kernel32.CreatePipe(
            ctypes.byref(read_handle),
            ctypes.byref(write_handle),
            ctypes.byref(attributes),
            0,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        read_value = int(read_handle.value)
        write_value = int(write_handle.value)
        if not _kernel32.SetHandleInformation(
            HANDLE(write_value), HANDLE_FLAG_INHERIT, 0
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            _close_handle(read_value)
            _close_handle(write_value)
            raise error
        return cls(read_value, write_value)

    def close_parent_read(self) -> None:
        _close_handle(self.read_handle)
        self.read_handle = 0

    def send(self, secret: bytearray) -> None:
        if not isinstance(secret, bytearray):
            raise TypeError("secret must be a bytearray")
        if len(secret) > MAX_SECRET_BYTES:
            wipe_bytearray(secret)
            raise SecretPipeError("secret payload is too large")
        header = bytearray(struct.pack("<I", len(secret)))
        try:
            if not self.write_handle:
                raise SecretPipeError("secret pipe write handle is closed")
            _write_all(self.write_handle, header)
            _write_all(self.write_handle, secret)
        finally:
            wipe_bytearray(header)
            wipe_bytearray(secret)
            _close_handle(self.write_handle)
            self.write_handle = 0

    def close(self) -> None:
        self.close_parent_read()
        _close_handle(self.write_handle)
        self.write_handle = 0

    def __enter__(self) -> "SecretPipe":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def read_secret(handle: int) -> bytearray:
    header = bytearray()
    try:
        header = _read_exact(handle, 4)
        size = struct.unpack("<I", header)[0]
        if size > MAX_SECRET_BYTES:
            raise SecretPipeError("secret payload is too large")
        return _read_exact(handle, size)
    finally:
        wipe_bytearray(header)
        _close_handle(handle)


@contextmanager
def credential_session(
    request: RecoveryRequest,
    secret_handle: int,
    *,
    client_factory: Callable[[str, RecoveryRequest], Any],
    secret_reader: Callable[[int], bytearray] = read_secret,
    api_factory: Callable[[], Any] | None = None,
    authorization_factory: Callable[[Any, str], tuple[str, str | None]] | None = None,
) -> Iterator[Any]:
    """Open the unchanged IIT binding with a password held only transiently."""

    if api_factory is None:
        from iit_eusign_ctypes import EUSignCP
        api_factory = EUSignCP
    if authorization_factory is None:
        from dps_recovery import create_verified_authorization
        authorization_factory = create_verified_authorization

    secret = bytearray()
    client = None
    try:
        secret = secret_reader(secret_handle)
        try:
            with api_factory() as api:
                try:
                    if request.jks_path is None or request.tax_id is None:
                        raise ValueError("credential fields are absent")
                    aliases = api.list_jks_aliases(Path(request.jks_path))
                    alias_index = request.alias_index
                    if alias_index is None or alias_index < 0 or alias_index >= len(aliases):
                        raise ValueError("alias index is invalid")
                    password_text = secret.decode("utf-8", errors="strict")
                    try:
                        api.read_jks_key(
                            Path(request.jks_path),
                            aliases[alias_index],
                            password_text,
                        )
                    finally:
                        password_text = ""
                    authorization, _signer = authorization_factory(api, request.tax_id)
                    client = client_factory(authorization, request)
                    authorization = ""
                except Exception:
                    raise CredentialSessionError("Не удалось открыть ключ КЭП") from None
                try:
                    yield client
                finally:
                    client.close()
                    client = None
        except CredentialSessionError:
            raise
    finally:
        wipe_bytearray(secret)


__all__ = [
    "CredentialSessionError",
    "SecretPipe",
    "SecretPipeError",
    "credential_session",
    "read_secret",
    "wipe_bytearray",
]
