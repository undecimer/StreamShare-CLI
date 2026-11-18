"""Encrypted on-disk session store for social account sessions.

Persists provider session blobs (e.g. Instagram settings) under
``data/sessions/`` encrypted with the shared at-rest Fernet scheme from
``core.security``. Legacy plaintext ``instagram_session*.json`` files are
migrated on first read.
"""

import json
import os
import re
from typing import Any, Dict, Optional

from .paths import get_data_dir
from .security import ENC_PREFIX, decrypt_scalar, encrypt_scalar

_SAFE_ID = re.compile(r"[^a-zA-Z0-9_.-]+")


def _sessions_dir() -> str:
    path = os.path.join(get_data_dir(), "sessions")
    os.makedirs(path, exist_ok=True)
    return path


def _session_path(account_id: str) -> str:
    safe = _SAFE_ID.sub("_", str(account_id or "default"))
    return os.path.join(_sessions_dir(), f"instagram_{safe}.enc")


def _legacy_paths(account_id: str) -> list:
    safe = _SAFE_ID.sub("_", str(account_id or "default"))
    return [
        os.path.join(os.getcwd(), f"instagram_session_{safe}.json"),
        os.path.join(os.getcwd(), "instagram_session.json") if safe == "default" else "",
    ]


def load_session(account_id: str) -> Optional[Dict[str, Any]]:
    """Return stored session settings for an account, or None."""
    path = _session_path(account_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
            plain = decrypt_scalar(raw)
            return json.loads(plain) if plain else None
        except Exception:
            return None

    # Migrate legacy plaintext session files
    for legacy in _legacy_paths(account_id):
        if legacy and os.path.exists(legacy):
            try:
                with open(legacy, "r") as f:
                    settings = json.load(f)
                save_session(account_id, settings)
                try:
                    os.remove(legacy)
                except OSError:
                    pass
                return settings
            except Exception:
                return None
    return None


def save_session(account_id: str, settings: Dict[str, Any]) -> None:
    """Persist session settings encrypted at rest."""
    if not settings:
        return
    token = encrypt_scalar(json.dumps(settings))
    if not token or not token.startswith(ENC_PREFIX):
        return
    path = _session_path(account_id)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write(token)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_session(account_id: str) -> None:
    try:
        os.remove(_session_path(account_id))
    except OSError:
        pass
