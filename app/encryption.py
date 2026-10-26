"""Encryption helpers for API key storage."""

import os
import sqlite3
from cryptography.fernet import Fernet, InvalidToken

from app import paths

_fernet = None


class KeyFileMissing(RuntimeError):
    """The key file is gone while the database holds keys encrypted with it."""


def forget_key():
    """Read the key file again on next use (after a restore)."""
    global _fernet
    _fernet = None


def _database_has_encrypted_keys() -> bool:
    db = paths.db_path()
    if not db.exists():
        return False
    conn = sqlite3.connect(str(db))
    try:
        return bool(conn.execute(
            "SELECT 1 FROM exchanges WHERE api_key_enc != '' LIMIT 1"
        ).fetchone())
    except sqlite3.OperationalError:  # no exchanges table yet: a new journal
        return False
    finally:
        conn.close()


def stored_keys_readable() -> bool:
    """True when every stored exchange key can be decrypted with the current key file."""
    if not _database_has_encrypted_keys():
        return True
    conn = sqlite3.connect(str(paths.db_path()))
    try:
        rows = conn.execute("SELECT api_key_enc FROM exchanges WHERE api_key_enc != ''").fetchall()
    finally:
        conn.close()
    try:
        for (value,) in rows:
            decrypt(value)
    except (InvalidToken, KeyFileMissing):
        return False
    return True


def _get_fernet() -> Fernet:
    """Get or create the Fernet encryption instance."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key_path = paths.encryption_key_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        key = key_path.read_bytes().strip()
    elif _database_has_encrypted_keys():
        # A new key would make every stored API key unreadable for good.
        raise KeyFileMissing(
            f"The encryption key file is missing ({key_path}) but the journal has saved exchange keys. "
            "Put the key file back from a backup. If you can't, remove each exchange and add its key again."
        )
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        # Restrict file permissions (best-effort on Windows)
        try:
            os.chmod(str(key_path), 0o600)
        except OSError:
            pass
        print(f"[encryption] Generated new encryption key at {key_path}")

    _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return base64-encoded ciphertext."""
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def fingerprint(plaintext: str) -> str:
    """Keyed hash of an API key: the same key always gives the same value, and
    without the local key file it says nothing about the key."""
    import hashlib
    import hmac

    _get_fernet()  # makes sure the key file exists
    secret = paths.encryption_key_path().read_bytes().strip()
    return hmac.new(secret, plaintext.encode("utf-8"), hashlib.sha256).hexdigest()


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext string."""
    if not ciphertext:
        return ""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
