"""Password hashing, opaque tokens, TOTP and the secret vault."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings

_ph = PasswordHasher()  # Argon2id defaults
MIN_PASSWORD = 10


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hashed: str | None, password: str) -> bool:
    if not hashed:
        # Equalise timing for unknown users.
        _ph.hash(password)
        return False
    try:
        return _ph.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD:
        problems.append(f"Use at least {MIN_PASSWORD} characters")
    if password.lower() == password and password.upper() == password and not any(c.isdigit() for c in password):
        problems.append("Mix letters with numbers or symbols")
    return problems


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hmac_sha256(key: str | bytes, message: str | bytes) -> str:
    k = key.encode() if isinstance(key, str) else key
    m = message.encode() if isinstance(message, str) else message
    return hmac.new(k, m, hashlib.sha256).hexdigest()


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ---- TOTP -----------------------------------------------------------------
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str, issuer: str = "VOLTORA") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    return bool(code) and pyotp.TOTP(secret).verify(code, valid_window=1)


def new_recovery_codes(n: int = 8) -> list[str]:
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(n)]


# ---- Vault ----------------------------------------------------------------
def _fernet() -> Fernet:
    s = get_settings()
    if s.vault_key:
        return Fernet(s.vault_key.encode())
    # Development only: derive from secret_key (config refuses this outside development).
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"voltora-vault", info=b"dev").derive(s.secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def seal(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def unseal(sealed: str) -> str:
    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except InvalidToken as exc:  # wrong key or tampering
        raise ValueError("Secret cannot be decrypted with the current vault key") from exc


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return "••••" + value[-4:] if len(value) > 8 else "••••"
