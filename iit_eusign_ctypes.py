"""Minimal ctypes interop for the official IIT EUSignCP.dll.

This module implements no cryptographic algorithm. All cryptographic operations
are delegated to the installed official DLL. Only declarations documented by
IIT are bound.
"""

from __future__ import annotations

import ctypes
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_DLL = Path(
    r"C:\Program Files (x86)\Institute of Informational Technologies"
    r"\Certificate Authority-1.3\End User\EUSignCP.dll"
)

DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
BYTE = ctypes.c_ubyte
PBYTE = ctypes.POINTER(BYTE)
PDWORD = ctypes.POINTER(DWORD)
PSTR = ctypes.c_char_p
EU_WARNING_END_OF_ENUM = 0x0007


class EUCertOwnerInfo(ctypes.Structure):
    """EUSignMSWCPPAppendixA section A.4.2, EU_CERT_OWNER_INFO."""

    _fields_ = [
        ("bFilled", BOOL),
        ("pszIssuer", PSTR),
        ("pszIssuerCN", PSTR),
        ("pszSerial", PSTR),
        ("pszSubject", PSTR),
        ("pszSubjCN", PSTR),
        ("pszSubjOrg", PSTR),
        ("pszSubjOrgUnit", PSTR),
        ("pszSubjTitle", PSTR),
        ("pszSubjState", PSTR),
        ("pszSubjLocality", PSTR),
        ("pszSubjFullName", PSTR),
        ("pszSubjAddress", PSTR),
        ("pszSubjPhone", PSTR),
        ("pszSubjEMail", PSTR),
        ("pszSubjDNS", PSTR),
        ("pszSubjEDRPOUCode", PSTR),
        ("pszSubjDRFOCode", PSTR),
    ]


class SystemTime(ctypes.Structure):
    _fields_ = [
        ("wYear", ctypes.c_uint16),
        ("wMonth", ctypes.c_uint16),
        ("wDayOfWeek", ctypes.c_uint16),
        ("wDay", ctypes.c_uint16),
        ("wHour", ctypes.c_uint16),
        ("wMinute", ctypes.c_uint16),
        ("wSecond", ctypes.c_uint16),
        ("wMilliseconds", ctypes.c_uint16),
    ]


class EUSignInfo(ctypes.Structure):
    """EUSignMSWCPPAppendixA section A.4.8, EU_SIGN_INFO."""

    _fields_ = EUCertOwnerInfo._fields_ + [
        ("bTimeAvail", BOOL),
        ("bTimeStamp", BOOL),
        ("Time", SystemTime),
    ]


@dataclass(frozen=True)
class CertificateSummary:
    common_name: str | None
    organization: str | None
    edrpou: str | None
    drfo: str | None
    certificate_count: int


@dataclass(frozen=True)
class VerificationResult:
    data: bytes
    certificate_present: bool
    signer_common_name: str | None


class IITError(RuntimeError):
    def __init__(self, operation: str, code: int):
        super().__init__(f"{operation} failed with IIT status {code}")
        self.operation = operation
        self.code = code


def _default_loader(path: str):
    if struct.calcsize("P") != 4:
        raise RuntimeError("EUSignCP.dll is x86; run this module with 32-bit CPython")
    return ctypes.WinDLL(path, use_last_error=True)


def _default_unloader(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    free_library = kernel32.FreeLibrary
    free_library.argtypes = [ctypes.c_void_p]
    free_library.restype = BOOL
    if not free_library(handle):
        raise ctypes.WinError(ctypes.get_last_error())


class EUSignCP:
    """Lifecycle wrapper for the documented no-key ABI surface."""

    def __init__(
        self,
        dll_path: Path | str = DEFAULT_DLL,
        *,
        loader: Callable[[str], object] = _default_loader,
        unloader: Callable[[int], None] = _default_unloader,
    ) -> None:
        self.dll_path = Path(dll_path)
        self._unloader = unloader
        self._dll = loader(str(self.dll_path))
        self._initialized = False
        self._private_key_loaded = False
        self._closed = False
        self._bind_abi()

    def _bind_abi(self) -> None:
        # Official source: EUSignMSWCPPAppendixA, section A.5.1, EUInitialize:
        # typedef DWORD (WINAPI *PEU_INITIALIZE)();
        self._dll.EUInitialize.argtypes = []
        self._dll.EUInitialize.restype = DWORD

        # Official source: EUSignMSWCPPAppendixA, section A.5.1, EUIsInitialized:
        # typedef BOOL (WINAPI *PEU_IS_INITIALIZED)();
        self._dll.EUIsInitialized.argtypes = []
        self._dll.EUIsInitialized.restype = BOOL

        # Official source: EUSignMSWCPPAppendixA, section A.5.1, EUFinalize:
        # typedef VOID (WINAPI *PEU_FINALIZE)();
        self._dll.EUFinalize.argtypes = []
        self._dll.EUFinalize.restype = None

    def _bind_jks_abi(self) -> None:
        if getattr(self, "_jks_bound", False):
            return

        # Official source: EUSignMSWCPPAppendixA, EUEnumJKSPrivateKeysFile:
        # typedef DWORD (WINAPI *PEU_ENUM_JKS_PRIVATE_KEYS_FILE)(
        #   PSTR pszFileName, DWORD dwIndex, PSTR *ppszKeyAlias);
        f = self._dll.EUEnumJKSPrivateKeysFile
        f.argtypes = [PSTR, DWORD, ctypes.POINTER(PSTR)]
        f.restype = DWORD

        # Official source: EUSignMSWCPPAppendixA, EUGetJKSPrivateKeyFile:
        # typedef DWORD (WINAPI *PEU_GET_JKS_PRIVATE_KEY_FILE)(PSTR, PSTR,
        #   PBYTE*, PDWORD, PDWORD, PBYTE**, PDWORD*);
        f = self._dll.EUGetJKSPrivateKeyFile
        f.argtypes = [
            PSTR, PSTR, ctypes.POINTER(PBYTE), PDWORD, PDWORD,
            ctypes.POINTER(ctypes.POINTER(PBYTE)), ctypes.POINTER(PDWORD),
        ]
        f.restype = DWORD

        # Official source: EUSignMSWCPPAppendixA, EUReadPrivateKeyBinary:
        # typedef DWORD (WINAPI *PEU_READ_PRIVATE_KEY_BINARY)(
        #   PBYTE, DWORD, PSTR, PEU_CERT_OWNER_INFO);
        f = self._dll.EUReadPrivateKeyBinary
        f.argtypes = [PBYTE, DWORD, PSTR, ctypes.POINTER(EUCertOwnerInfo)]
        f.restype = DWORD

        # Official source: EUSignMSWCPPAppendixA, EUResetPrivateKey:
        # typedef VOID (WINAPI *PEU_RESET_PRIVATE_KEY)();
        self._dll.EUResetPrivateKey.argtypes = []
        self._dll.EUResetPrivateKey.restype = None

        # Official source: EUSignMSWCPPAppendixA, EUFreeMemory:
        # typedef VOID (WINAPI *PEU_FREE_MEMORY)(PBYTE pbMemory);
        self._dll.EUFreeMemory.argtypes = [PBYTE]
        self._dll.EUFreeMemory.restype = None

        # Official source: EUSignMSWCPPAppendixA, EUFreeCertOwnerInfo:
        # typedef VOID (WINAPI *PEU_FREE_CERT_OWNER_INFO)(PEU_CERT_OWNER_INFO);
        self._dll.EUFreeCertOwnerInfo.argtypes = [ctypes.POINTER(EUCertOwnerInfo)]
        self._dll.EUFreeCertOwnerInfo.restype = None

        # Official source: EUSignMSWCPPAppendixA, EUFreeCertificatesArray:
        # typedef VOID (WINAPI *PEU_FREE_CERTIFICATES_ARRAY)(
        #   DWORD, PBYTE*, PDWORD);
        self._dll.EUFreeCertificatesArray.argtypes = [
            DWORD, ctypes.POINTER(PBYTE), PDWORD,
        ]
        self._dll.EUFreeCertificatesArray.restype = None
        self._jks_bound = True

    def _bind_sign_abi(self) -> None:
        if getattr(self, "_sign_bound", False):
            return
        self._bind_jks_abi()

        # Official source: EUSignMSWCPPAppendixA, EUSignDataInternal:
        # typedef DWORD (WINAPI *PEU_SIGN_DATA_INTERNAL)(
        #   BOOL, PBYTE, DWORD, PSTR*, PBYTE*, PDWORD);
        f = self._dll.EUSignDataInternal
        f.argtypes = [
            BOOL, PBYTE, DWORD, ctypes.POINTER(PSTR),
            ctypes.POINTER(PBYTE), PDWORD,
        ]
        f.restype = DWORD

        # Official source: EUSignMSWCPPAppendixA, EUVerifyDataInternal:
        # typedef DWORD (WINAPI *PEU_VERIFY_DATA_INTERNAL)(
        #   PSTR, PBYTE, DWORD, PBYTE*, PDWORD, PEU_SIGN_INFO);
        f = self._dll.EUVerifyDataInternal
        f.argtypes = [
            PSTR, PBYTE, DWORD, ctypes.POINTER(PBYTE), PDWORD,
            ctypes.POINTER(EUSignInfo),
        ]
        f.restype = DWORD

        # Official source: EUSignMSWCPPAppendixA,
        # EUGetCertificateFromSignedData:
        # typedef DWORD (WINAPI *PEU_GET_CERTIFICATE_FROM_SIGNED_DATA)(
        #   DWORD, PSTR, PBYTE, DWORD, PPEU_CERT_INFO_EX, PBYTE*, PDWORD);
        f = self._dll.EUGetCertificateFromSignedData
        f.argtypes = [
            DWORD, PSTR, PBYTE, DWORD, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(PBYTE), PDWORD,
        ]
        f.restype = DWORD

        # Official source: EUSignMSWCPPAppendixA, EUFreeSignInfo:
        # typedef VOID (WINAPI *PEU_FREE_SIGN_INFO)(PEU_SIGN_INFO);
        self._dll.EUFreeSignInfo.argtypes = [ctypes.POINTER(EUSignInfo)]
        self._dll.EUFreeSignInfo.restype = None

        # Official source: EUSignMSWCPPAppendixA, EUFreeCertificateInfoEx:
        # typedef VOID (WINAPI *PEU_FREE_CERTIFICATE_INFO_EX)(PEU_CERT_INFO_EX);
        self._dll.EUFreeCertificateInfoEx.argtypes = [ctypes.c_void_p]
        self._dll.EUFreeCertificateInfoEx.restype = None
        self._sign_bound = True

    @staticmethod
    def _encode_c_string(value: str) -> bytes:
        # EUSignMSWCPPAppendixA section A.4: strings default to CP1251.
        return value.encode("cp1251", errors="strict")

    @staticmethod
    def _decode_c_string(value: bytes | None) -> str | None:
        return None if value is None else value.decode("cp1251", errors="strict")

    def list_jks_aliases(self, path: Path | str) -> list[bytes]:
        self._bind_jks_abi()
        path_bytes = self._encode_c_string(str(Path(path)))
        aliases: list[bytes] = []
        for index in range(1024):
            alias_ptr = PSTR()
            result = int(
                self._dll.EUEnumJKSPrivateKeysFile(path_bytes, index, ctypes.byref(alias_ptr))
            )
            if result == EU_WARNING_END_OF_ENUM:
                return aliases
            if result != 0:
                raise IITError("EUEnumJKSPrivateKeysFile", result)
            try:
                aliases.append(ctypes.string_at(alias_ptr))
            finally:
                self._dll.EUFreeMemory(ctypes.cast(alias_ptr, PBYTE))
        raise RuntimeError("JKS alias enumeration exceeded the safety limit")

    def read_jks_key(
        self, path: Path | str, alias: bytes, password: str
    ) -> CertificateSummary:
        self._bind_jks_abi()
        path_bytes = self._encode_c_string(str(Path(path)))
        private_key = PBYTE()
        private_key_length = DWORD()
        certificate_count = DWORD()
        certificates = ctypes.POINTER(PBYTE)()
        certificate_lengths = PDWORD()
        owner = EUCertOwnerInfo()
        password_bytes = bytearray(self._encode_c_string(password))
        password_buffer = (ctypes.c_char * (len(password_bytes) + 1))()
        password_buffer[: len(password_bytes)] = password_bytes

        result = int(
            self._dll.EUGetJKSPrivateKeyFile(
                path_bytes,
                alias,
                ctypes.byref(private_key),
                ctypes.byref(private_key_length),
                ctypes.byref(certificate_count),
                ctypes.byref(certificates),
                ctypes.byref(certificate_lengths),
            )
        )
        if result != 0:
            for i in range(len(password_bytes)):
                password_bytes[i] = 0
            ctypes.memset(password_buffer, 0, len(password_buffer))
            raise IITError("EUGetJKSPrivateKeyFile", result)

        try:
            result = int(
                self._dll.EUReadPrivateKeyBinary(
                    private_key,
                    private_key_length,
                    ctypes.cast(password_buffer, PSTR),
                    ctypes.byref(owner),
                )
            )
            if result != 0:
                raise IITError("EUReadPrivateKeyBinary", result)
            self._private_key_loaded = True
            if not owner.bFilled:
                raise RuntimeError("IIT did not return certificate owner information")
            return CertificateSummary(
                common_name=self._decode_c_string(owner.pszSubjCN),
                organization=self._decode_c_string(owner.pszSubjOrg),
                edrpou=self._decode_c_string(owner.pszSubjEDRPOUCode),
                drfo=self._decode_c_string(owner.pszSubjDRFOCode),
                certificate_count=int(certificate_count.value),
            )
        finally:
            for i in range(len(password_bytes)):
                password_bytes[i] = 0
            ctypes.memset(password_buffer, 0, len(password_buffer))
            if owner.bFilled:
                self._dll.EUFreeCertOwnerInfo(ctypes.byref(owner))
            if private_key:
                self._dll.EUFreeMemory(private_key)
            if certificates or certificate_lengths:
                self._dll.EUFreeCertificatesArray(
                    certificate_count, certificates, certificate_lengths
                )

    def sign_data_internal(self, data: bytes, append_certificate: bool = True) -> bytes:
        self._bind_sign_abi()
        if not self._private_key_loaded:
            raise RuntimeError("Private key is not loaded")
        if not data:
            raise ValueError("Data to sign must not be empty")
        data_buffer = (BYTE * len(data)).from_buffer_copy(data)
        signed_text = PSTR()
        signed_binary = PBYTE()
        signed_length = DWORD()
        result = int(
            self._dll.EUSignDataInternal(
                BOOL(bool(append_certificate)),
                ctypes.cast(data_buffer, PBYTE),
                DWORD(len(data)),
                ctypes.byref(signed_text),
                ctypes.byref(signed_binary),
                ctypes.byref(signed_length),
            )
        )
        if result != 0:
            raise IITError("EUSignDataInternal", result)
        try:
            if not signed_text:
                raise RuntimeError("IIT did not return Base64 signed data")
            return ctypes.string_at(signed_text)
        finally:
            if signed_text:
                self._dll.EUFreeMemory(ctypes.cast(signed_text, PBYTE))
            if signed_binary:
                self._dll.EUFreeMemory(signed_binary)

    def verify_data_internal(self, signed_base64: bytes) -> VerificationResult:
        self._bind_sign_abi()
        recovered = PBYTE()
        recovered_length = DWORD()
        sign_info = EUSignInfo()
        result = int(
            self._dll.EUVerifyDataInternal(
                signed_base64,
                PBYTE(),
                DWORD(0),
                ctypes.byref(recovered),
                ctypes.byref(recovered_length),
                ctypes.byref(sign_info),
            )
        )
        if result != 0:
            raise IITError("EUVerifyDataInternal", result)
        try:
            recovered_bytes = ctypes.string_at(recovered, recovered_length.value)
            signer_cn = (
                self._decode_c_string(sign_info.pszSubjCN)
                if sign_info.bFilled else None
            )
        finally:
            if recovered:
                self._dll.EUFreeMemory(recovered)
            if sign_info.bFilled:
                self._dll.EUFreeSignInfo(ctypes.byref(sign_info))

        cert_info = ctypes.c_void_p()
        certificate = PBYTE()
        certificate_length = DWORD()
        result = int(
            self._dll.EUGetCertificateFromSignedData(
                DWORD(0),
                signed_base64,
                PBYTE(),
                DWORD(0),
                ctypes.byref(cert_info),
                ctypes.byref(certificate),
                ctypes.byref(certificate_length),
            )
        )
        try:
            if result != 0:
                raise IITError("EUGetCertificateFromSignedData", result)
            certificate_present = bool(certificate and certificate_length.value)
        finally:
            if cert_info:
                self._dll.EUFreeCertificateInfoEx(cert_info)
            if certificate:
                self._dll.EUFreeMemory(certificate)

        return VerificationResult(
            data=recovered_bytes,
            certificate_present=certificate_present,
            signer_common_name=signer_cn,
        )

    def initialize(self) -> None:
        result = int(self._dll.EUInitialize())
        if result != 0:
            raise IITError("EUInitialize", result)
        self._initialized = True

    def is_initialized(self) -> bool:
        return bool(self._dll.EUIsInitialized())

    def finalize(self) -> None:
        if self._initialized:
            if self._private_key_loaded:
                self._dll.EUResetPrivateKey()
                self._private_key_loaded = False
            self._dll.EUFinalize()
            self._initialized = False

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.finalize()
        finally:
            self._unloader(self._dll._handle)
            self._closed = True

    def __enter__(self) -> "EUSignCP":
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
