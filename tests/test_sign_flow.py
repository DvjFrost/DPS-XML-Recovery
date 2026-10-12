import ctypes

import pytest

from iit_eusign_ctypes import EUSignInfo, SystemTime


def encode_tax_id(value: str) -> bytes:
    if len(value) not in (8, 10) or not value.isascii() or not value.isdigit():
        raise ValueError("Expected exactly 8 or 10 ASCII digits")
    return value.encode("ascii")


@pytest.mark.parametrize("value", ["12345678", "1234567890"])
def test_tax_id_is_exact_ascii_without_bom_or_newline(value):
    assert encode_tax_id(value) == bytes(ord(char) for char in value)


@pytest.mark.parametrize(
    "value", [" 12345678", "12345678\n", "+12345678", "１２３４５６７８", "123"]
)
def test_tax_id_rejects_normalization_and_non_ascii(value):
    with pytest.raises(ValueError):
        encode_tax_id(value)


def test_sign_info_native_layout():
    assert ctypes.sizeof(SystemTime) == 16
    expected = 96 if ctypes.sizeof(ctypes.c_void_p) == 4 else 168
    assert ctypes.sizeof(EUSignInfo) == expected
