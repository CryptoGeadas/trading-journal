"""Encryption helpers for API key storage."""

import os
from pathlib import Path
from cryptography.fernet import Fernet

_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / ".encryption_key"
_fernet = None


def _get_fernet() -> Fernet:
    """Get or create the Fernet encryption instance."""
    global _fernet
    if _fernet is not None:
        return _fernet

    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)

    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        _KEY_PATH.write_bytes(key)
        # Restrict file permissions (best-effort on Windows)
        try:
            os.chmod(str(_KEY_PATH), 0o600)
        except OSError:
            pass
        print(f"[encryption] Generated new encryption key at {_KEY_PATH}")

    _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return base64-encoded ciphertext."""
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext string."""
    if not ciphertext:
        return ""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
