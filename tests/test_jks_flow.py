import ctypes

from iit_eusign_ctypes import EUCertOwnerInfo


def test_cert_owner_info_native_layout_matches_documented_fields():
    assert [name for name, _ in EUCertOwnerInfo._fields_] == [
        "bFilled", "pszIssuer", "pszIssuerCN", "pszSerial", "pszSubject",
        "pszSubjCN", "pszSubjOrg", "pszSubjOrgUnit", "pszSubjTitle",
        "pszSubjState", "pszSubjLocality", "pszSubjFullName",
        "pszSubjAddress", "pszSubjPhone", "pszSubjEMail", "pszSubjDNS",
        "pszSubjEDRPOUCode", "pszSubjDRFOCode",
    ]
    expected = 72 if ctypes.sizeof(ctypes.c_void_p) == 4 else 144
    assert ctypes.sizeof(EUCertOwnerInfo) == expected
