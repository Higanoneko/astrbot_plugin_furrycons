import asyncio
import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .errors import SourceError
from .models import Convention

logger = logging.getLogger(__name__)


class CatalogCache:
    """Only complete catalogs are stored; all query filters run after this boundary."""

    def __init__(self, path=":memory:"):
        # This cache is optional: do not stall the host's event loop waiting for
        # another process to release a database lock.
        self.connection = sqlite3.connect(path, timeout=0)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS catalogs (source TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT NOT NULL)"
        )
        self.connection.commit()
        self._lock = asyncio.Lock()

    def _read(self, key):
        row = self.connection.execute(
            "SELECT fetched, payload FROM catalogs WHERE source = ?", (key,)
        ).fetchone()
        if not row:
            return None
        try:
            return row[0], [Convention(**e) for e in json.loads(row[1])]
        except (ValueError, TypeError, KeyError):
            return None

    def _write(self, key, fetched, events):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO catalogs VALUES (?, ?, ?)",
                (key, fetched, json.dumps([asdict(e) for e in events], ensure_ascii=False)),
            )

    async def load(self, source, settings, clock, *, refresh=False):
        # Credential changes may change the visible dataset; retain only a digest.
        key = hashlib.sha256(
            (settings.api_base.rstrip("/") + "\0" + settings.api_key).encode()
        ).hexdigest()
        async with self._lock:
            try:
                cached = self._read(key)
            except sqlite3.Error:
                logger.warning("目录缓存读取失败，本次尝试实时获取展会资料。")
                cached = None
            age = clock().timestamp() - cached[0] if cached else None
            stale = False
            if not refresh and cached and 0 <= age < settings.cache_minutes * 60:
                fetched, events = cached
            else:
                try:
                    events = await source.list_all()
                    fetched = clock().timestamp()
                    try:
                        self._write(key, fetched, events)
                    except sqlite3.Error:
                        # The catalog is optional: a failed cache write must not
                        # discard fresh data already obtained for this query.
                        pass
                except SourceError:
                    age = clock().timestamp() - cached[0] if cached else None
                    if not (
                        cached
                        and settings.stale_hours > 0
                        and 0 <= age <= settings.stale_hours * 3600
                    ):
                        raise
                    fetched, events = cached
                    stale = True
            timestamp = (
                datetime.fromtimestamp(fetched, UTC)
                .astimezone(ZoneInfo(settings.timezone))
                .strftime("%Y-%m-%d %H:%M:%S %Z")
            )
            note = f"数据获取于 {timestamp}"
            if stale:
                note = "数据源读取失败，使用旧缓存 · " + note
            return events, note

    def close(self):
        self.connection.close()
