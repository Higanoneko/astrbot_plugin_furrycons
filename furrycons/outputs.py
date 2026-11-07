"""Persistent, chat-scoped references to the exact generated image files."""

import hashlib
import logging
import sqlite3
from pathlib import Path
from uuid import uuid4

from .errors import UserError

logger = logging.getLogger(__name__)


class OutputStore:
    def __init__(self, path=":memory:", image_root=None):
        self.command_entry = "FEC"
        self.connection = sqlite3.connect(path)
        self.image_root = Path(image_root).resolve() if image_root else None
        self._prune_cursor = None
        self.prune_pending = False
        self.connection.execute("""CREATE TABLE IF NOT EXISTS outputs (
            code TEXT PRIMARY KEY, chat TEXT NOT NULL, path TEXT NOT NULL,
            digest TEXT, expires REAL NOT NULL)""")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS outputs_expiry ON outputs (expires, code)"
        )

    def _path(self, path):
        path = Path(path).resolve()
        if self.image_root and not path.is_relative_to(self.image_root):
            raise UserError("原文件不在插件图片目录内，请重新查询。")
        return path

    def register(self, path, chat, now, lifetime):
        path = self._path(path)
        try:
            digest = None
            if path.is_file():
                with path.open("rb") as original:
                    digest = hashlib.file_digest(original, "sha256").hexdigest()
            elif self.image_root:
                raise UserError("生成原文件已丢失，请重新查询。")
        except OSError:
            raise UserError("生成原文件无法读取，请检查插件数据目录后重试。") from None
        for _ in range(10):
            code = uuid4().hex[:10].upper()
            try:
                with self.connection:
                    self.connection.execute(
                        "INSERT INTO outputs VALUES (?, ?, ?, ?, ?)",
                        (code, chat, str(path), digest, now.timestamp() + lifetime),
                    )
                return code
            except sqlite3.IntegrityError:
                continue
        raise UserError("暂时无法分配输出码，请重试。")

    def get(self, code, chat, now):
        row = self.connection.execute(
            "SELECT path, digest, expires FROM outputs WHERE code=? AND chat=?",
            (code.upper(), chat),
        ).fetchone()
        if row is None:
            raise UserError(
                f"当前聊天中没有该输出，可能已过期；请核对图片附带的 /{self.command_entry} 原图 <输出码> 或重新查询。"
            )
        if now.timestamp() >= row[2]:
            raise UserError("原文件获取入口已过期，请重新查询生成图片。")
        path = self._path(row[0])
        try:
            with path.open("rb") as original:
                digest = hashlib.file_digest(original, "sha256").hexdigest()
        except OSError:
            raise UserError("生成原文件已丢失或不可读，请重新查询生成图片。") from None
        if row[1] is None or digest != row[1]:
            raise UserError("生成原文件已发生变化，未发送替代文件，请重新查询。")
        return path

    def prune(self, now, *, grace=86400, limit=100, protected=()):
        cutoff = now.timestamp() - grace
        protected = set(protected)
        cursor = self._prune_cursor
        rows = self.connection.execute(
            "SELECT code, path, expires FROM outputs WHERE expires <= ? "
            "AND (? IS NULL OR expires > ? OR (expires = ? AND code > ?)) "
            "ORDER BY expires, code LIMIT ?",
            (cutoff, cursor[0] if cursor else None, cursor[0] if cursor else None,
             cursor[0] if cursor else None, cursor[1] if cursor else None, limit + 1),
        ).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        removed = 0
        for code, stored, _expires in rows:
            path = Path(stored)
            try:
                if self.image_root is not None:
                    resolved = path.resolve()
                    if not path.is_absolute() or resolved != path or not resolved.is_relative_to(self.image_root):
                        logger.warning("跳过路径不安全的过期输出记录：%s", code)
                        continue
                    if str(resolved) in protected:
                        continue
                    shared = self.connection.execute(
                        "SELECT 1 FROM outputs WHERE path = ? AND expires > ? LIMIT 1",
                        (stored, cutoff),
                    ).fetchone()
                    if not shared:
                        path.unlink(missing_ok=True)
                with self.connection:
                    self.connection.execute("DELETE FROM outputs WHERE code = ?", (code,))
                removed += 1
            except (OSError, RuntimeError):
                logger.warning("无法清理过期输出记录 %s，稍后重试。", code, exc_info=True)
        self.prune_pending = more
        self._prune_cursor = (rows[-1][2], rows[-1][0]) if more else None
        return removed

    def close(self):
        self.connection.close()
