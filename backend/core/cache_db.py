"""Local SQLite cache for IGDB game metadata and artwork files.

Drop-in replacement for the ecosystem's SQLAlchemy ``CachedGame`` table,
backed by stdlib sqlite3 so the CLI needs no ORM. One row per cached game;
lookups mirror the original precedence (Twitch category id, normalized
slug, case-insensitive name).
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .paths import get_data_dir

_TTL_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cached_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_name TEXT,
    normalized_slug TEXT,
    twitch_category_id TEXT,
    igdb_id INTEGER,
    cover_filename TEXT,
    artwork_filename TEXT,
    cover_url TEXT,
    artwork_url TEXT,
    genres TEXT,
    summary TEXT,
    expires_at TEXT
)
"""


def _db_path() -> str:
    path = os.path.join(get_data_dir(), "igdb_cache.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def _row_to_record(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    record = dict(row)
    genres = record.get("genres")
    if isinstance(genres, str):
        try:
            record["genres"] = json.loads(genres)
        except Exception:
            record["genres"] = None
    return record


def find_cached_game(twitch_id: Optional[str] = None, game_name: Optional[str] = None,
                     normalized_slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Find a cached game by Twitch category id, slug, then name."""
    conn = _connect()
    try:
        if twitch_id and str(twitch_id) not in ("None", "0", ""):
            row = conn.execute(
                "SELECT * FROM cached_games WHERE twitch_category_id = ? LIMIT 1",
                (str(twitch_id),),
            ).fetchone()
            if row:
                return _row_to_record(row)
        if normalized_slug:
            row = conn.execute(
                "SELECT * FROM cached_games WHERE normalized_slug = ? LIMIT 1",
                (normalized_slug,),
            ).fetchone()
            if row:
                return _row_to_record(row)
        if game_name:
            row = conn.execute(
                "SELECT * FROM cached_games WHERE lower(game_name) = lower(?) LIMIT 1",
                (game_name.strip(),),
            ).fetchone()
            if row:
                return _row_to_record(row)
        return None
    finally:
        conn.close()


def upsert_cached_game(record: Dict[str, Any]) -> None:
    """Insert or update a cached game keyed on its existing row id when present."""
    fields = dict(record)
    genres = fields.get("genres")
    if genres is not None and not isinstance(genres, str):
        fields["genres"] = json.dumps(genres)
    conn = _connect()
    try:
        if fields.get("id"):
            cols = [k for k in fields if k != "id"]
            conn.execute(
                f"UPDATE cached_games SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                [fields[c] for c in cols] + [fields["id"]],
            )
        else:
            cols = list(fields)
            conn.execute(
                f"INSERT INTO cached_games ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [fields[c] for c in cols],
            )
            fields["id"] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            record["id"] = fields["id"]
        conn.commit()
    finally:
        conn.close()


def all_cached_games() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM cached_games").fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def delete_placeholder_games() -> int:
    """Drop unhelpful raw placeholder records like 'Game <number>'."""
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM cached_games WHERE game_name LIKE 'Game %'")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def fresh_expiry() -> str:
    """ISO timestamp for a new record's TTL horizon."""
    return (datetime.now(timezone.utc) + timedelta(days=_TTL_DAYS)).isoformat()


def is_expired(record: Dict[str, Any]) -> bool:
    """Whether a cached record has passed its TTL."""
    raw = record.get("expires_at")
    if not raw:
        return True
    try:
        expires_at = datetime.fromisoformat(str(raw))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires_at
