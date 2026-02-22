"""Encrypted credential management using Fernet symmetric encryption.

The encryption key is derived from the current Windows user identity
(username + hostname) via PBKDF2HMAC, binding the encrypted data to
the specific user/machine — similar to Windows DPAPI.

Encrypted values are stored with an ``ENC:`` prefix so plain-text
legacy values can be transparently migrated on first read.
"""

import base64
import os
import platform

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Fixed application salt (not secret — just avoids rainbow-table reuse)
_APP_SALT = b"FSEProcessor-v2-credential-salt"

_ENC_PREFIX = "ENC:"


def _derive_key() -> bytes:
    """Derive a Fernet key from the current Windows user + machine identity."""
    identity = (os.getlogin() + platform.node()).encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_APP_SALT,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(identity))


def encrypt_password(plain: str) -> str:
    """Encrypt a plain-text password and return an ``ENC:`` prefixed token."""
    if not plain:
        return ""
    key = _derive_key()
    token = Fernet(key).encrypt(plain.encode("utf-8"))
    return _ENC_PREFIX + token.decode("ascii")


def decrypt_password(stored: str) -> str:
    """Decrypt a stored password value.

    If the value has the ``ENC:`` prefix it is decrypted; otherwise the
    plain text is returned as-is (pre-migration compatibility).

    Returns an empty string if decryption fails (wrong user/machine).
    """
    if not stored:
        return ""
    if not stored.startswith(_ENC_PREFIX):
        return stored  # plain text — pre-migration
    token = stored[len(_ENC_PREFIX):]
    try:
        key = _derive_key()
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        return ""


def verify_password(plain: str, stored: str) -> bool:
    """Check whether *plain* matches the *stored* (possibly encrypted) value."""
    decrypted = decrypt_password(stored)
    return plain == decrypted


def is_encrypted(stored: str) -> bool:
    """Return ``True`` if the stored value carries the ``ENC:`` prefix."""
    return stored.startswith(_ENC_PREFIX) if stored else False
