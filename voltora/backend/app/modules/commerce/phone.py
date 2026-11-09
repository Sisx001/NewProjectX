"""Bangladesh mobile numbers: one canonical local form (01XXXXXXXXX) everywhere, E.164 on demand."""
from __future__ import annotations

import re

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_LOCAL = re.compile(r"^01[3-9]\d{8}$")


class InvalidPhone(ValueError):
    pass


def normalize_bd_mobile(raw: str | None) -> str:
    """'+880 1711-234567', '8801711234567', '01711234567', '০১৭১১২৩৪৫৬৭' → '01711234567'."""
    if not raw:
        raise InvalidPhone("Enter a mobile number")
    s = raw.strip().translate(_BN_DIGITS)
    s = re.sub(r"[\s\-().]", "", s)
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("00"):
        s = s[2:]
    if s.startswith("880"):
        s = s[3:]
        if not s.startswith("0"):
            s = "0" + s
    if not _LOCAL.match(s):
        raise InvalidPhone("Enter a valid Bangladesh mobile number (01XXXXXXXXX)")
    return s


def is_bd_mobile(raw: str | None) -> bool:
    try:
        normalize_bd_mobile(raw)
        return True
    except InvalidPhone:
        return False


def to_e164(raw: str) -> str:
    return "+880" + normalize_bd_mobile(raw)[1:]


def mask_phone(local: str | None) -> str:
    if not local:
        return ""
    return local[:3] + "•••••" + local[-3:] if len(local) >= 8 else "•••"
