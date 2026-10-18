"""Theme preferences live outside theme package folders and plugin source."""

import json
import sqlite3

from .errors import UserError


class ThemePreferences:
    def __init__(self, path=":memory:"):
        self.path = path
        self._connection = None

    @property
    def connection(self):
        if self._connection is None:
            self._connection = sqlite3.connect(self.path)
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS theme_defaults (scope TEXT PRIMARY KEY, theme_key TEXT NOT NULL)"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS theme_colors (theme_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS theme_origins (theme_key TEXT PRIMARY KEY, origin_key TEXT NOT NULL)"
            )
        return self._connection

    def default(self, chat=None):
        scope = "global" if chat is None else "chat:" + chat
        row = self.connection.execute(
            "SELECT theme_key FROM theme_defaults WHERE scope=?", (scope,)
        ).fetchone()
        return row[0] if row else None

    def sessions(self):
        return [
            {"chat": scope.removeprefix("chat:"), "key": key}
            for scope, key in self.connection.execute(
                "SELECT scope, theme_key FROM theme_defaults WHERE scope != 'global' ORDER BY scope"
            )
        ]

    def set_default(self, theme_key, chat=None):
        scope = "global" if chat is None else "chat:" + chat
        with self.connection:
            if theme_key is None:
                self.connection.execute("DELETE FROM theme_defaults WHERE scope=?", (scope,))
            else:
                self.connection.execute(
                    "INSERT OR REPLACE INTO theme_defaults VALUES (?, ?)", (scope, theme_key)
                )

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def colors(self, key):
        row = self.connection.execute(
            "SELECT payload FROM theme_colors WHERE theme_key=?", (key,)
        ).fetchone()
        try:
            return json.loads(row[0]) if row else {}
        except (ValueError, TypeError):
            raise UserError("主题配色数据无法读取，请机器人管理者检查插件数据目录。") from None

    def set_colors(self, key, colors):
        with self.connection:
            if colors:
                self.connection.execute(
                    "INSERT OR REPLACE INTO theme_colors VALUES (?, ?)",
                    (key, json.dumps(colors, ensure_ascii=False)),
                )
            else:
                self.connection.execute("DELETE FROM theme_colors WHERE theme_key=?", (key,))

    def origin(self, key):
        row = self.connection.execute(
            "SELECT origin_key FROM theme_origins WHERE theme_key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_origin(self, key, origin):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO theme_origins VALUES (?, ?)", (key, origin)
            )
