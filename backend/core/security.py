"""At-rest encryption for sensitive blobs (H2).

Social account credentials (Instagram passwords/session data, Twitter API
keys, Discord webhook URLs) are stored encrypted in SQLite instead of plain
JSON. The key is derived from the application SECRET_KEY (env var, or the
persisted data/jwt_secret.txt), so no extra key material is required.

Format: "enc:v1:<fernet-token>". Values without that prefix are treated as
legacy plaintext and are transparently returned as dicts (and re-encrypted
on their next write / by the startup migration in update_db_schema.py).
"""

import base64
import hashlib
import json
import os
from typing import Optional

from .paths import get_secret_path

ENC_PREFIX = "enc:v1:"


def _get_secret_material() -> bytes:
    env_secret = os.getenv("SECRET_KEY")
    if env_secret:
        return env_secret.encode()

    secret_path = get_secret_path()
    try:
        with open(secret_path, "r") as f:
            key = f.read().strip()
            if key:
                return key.encode()
    except Exception:
        pass

    # Last resort (matches auth._get_or_create_secret_key behaviour): create
    # and persist a fresh secret so previously-written blobs stay readable.
    import secrets as _secrets
    new_secret = _secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(secret_path), exist_ok=True)
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(new_secret)
    except Exception:
        pass
    return new_secret.encode()


def _fernet():
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(b"streamshare-field-key:" + _get_secret_material()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def dump_credentials(credentials: dict) -> str:
    """Serialize a credentials dict to its encrypted at-rest representation."""
    if credentials is None:
        return None
    if isinstance(credentials, str):
        # Already serialized; re-encrypt only if plaintext JSON.
        if credentials.startswith(ENC_PREFIX) or credentials.startswith(f'"{ENC_PREFIX}'):
            return credentials
        try:
            credentials = json.loads(credentials)
        except Exception:
            return credentials
    token = _fernet().encrypt(json.dumps(credentials).encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def load_credentials(stored):
    """Return a credentials dict from DB storage (encrypted string, JSON
    string, legacy dict or None)."""
    if stored is None:
        return {}
    if isinstance(stored, dict):
        return stored  # legacy plaintext row (until re-saved / migrated)

    if isinstance(stored, str):
        raw = stored.strip()
        # SQLAlchemy JSON column stores strings double-encoded.
        if raw.startswith('"') and raw.endswith('"'):
            try:
                raw = json.loads(raw)
            except Exception:
                pass

        if isinstance(raw, str) and raw.startswith(ENC_PREFIX):
            try:
                token = raw[len(ENC_PREFIX):].encode("ascii")
                return json.loads(_fernet().decrypt(token).decode("utf-8"))
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to decrypt account credentials: {e}")
                return {}

        # Legacy plaintext JSON
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}


def is_encrypted(stored) -> bool:
    if not isinstance(stored, str):
        return False
    raw = stored.strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            raw = json.loads(raw)
        except Exception:
            return False
    return isinstance(raw, str) and raw.startswith(ENC_PREFIX)


def encrypt_scalar(val: Optional[str]) -> Optional[str]:
    """Encrypt a scalar secret string at rest."""
    if not val or not isinstance(val, str):
        return val
    raw = val.strip()
    if raw.startswith(ENC_PREFIX):
        return raw
    token = _fernet().encrypt(raw.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt_scalar(val: Optional[str]) -> Optional[str]:
    """Decrypt an encrypted scalar secret string."""
    if not val or not isinstance(val, str):
        return val
    raw = val.strip()
    if not raw.startswith(ENC_PREFIX):
        return raw
    try:
        token = raw[len(ENC_PREFIX):].encode("ascii")
        return _fernet().decrypt(token).decode("utf-8")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to decrypt scalar secret: {e}")
        return ""

