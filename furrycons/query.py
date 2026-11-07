import json
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import UserError
from .models import Convention
from .regions import matches_region, query_region


def instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def is_undated(event):
    begin, finish = instant(event.start), instant(event.end)
    return event.status == "postponed" or begin is None or finish is None or finish < begin


def query_window(args, now, timezone):
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    include_cancelled = False
    days, dates = None, []
    for token in args:
        if token in ("显示取消", "--include-cancelled"):
            include_cancelled = True
        elif re.fullmatch(r"[0-9]{1,3}天", token):
            if days is not None:
                raise UserError("只能指定一次天数。")
            days = int(token[:-1])
            if not 1 <= days <= 365:
                raise UserError("查询天数须为 1–365 天。")
        elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", token):
            try:
                dates.append(date.fromisoformat(token))
            except ValueError:
                raise UserError("日期无效，请使用真实的 YYYY-MM-DD 日期。") from None
        else:
            raise UserError("查询参数无效。用法：查询 [30天 | 起始日期 结束日期] [显示取消]。")
    if dates and (len(dates) != 2 or days is not None):
        raise UserError("请指定起止两个日期，不能同时指定天数。")
    first = dates[0] if dates else today
    last = dates[1] if dates else today + timedelta(days=(days or 365) - 1)
    if first > last or last == date.max:
        raise UserError("日期范围无效，结束日期须不早于开始日期且早于 9999-12-31。")
    start = datetime.combine(first, time.min, zone)
    end = datetime.combine(last + timedelta(days=1), time.min, zone)
    label = f"{first.isoformat()} 至 {last.isoformat()} · {timezone}"
    if include_cancelled:
        label += " · 显示取消"
    return start, end, include_cancelled, label


def query_options(args, now, timezone):
    date_args, region_words = [], []
    for token in args:
        if token in ("显示取消", "--include-cancelled") or re.fullmatch(
            r"[0-9]{1,3}天|[0-9]{4}-[0-9]{2}-[0-9]{2}", token
        ):
            date_args.append(token)
        else:
            region_words.append(token)
    region = query_region("".join(region_words)) if region_words else None
    start, end, include_cancelled, label = query_window(date_args, now, timezone)
    if region:
        label += " · 地区：" + "".join(region)
    return start, end, include_cancelled, label, region


def default_query(conventions, now, timezone, args=()):
    start, end, include_cancelled, label, region = query_options(args, now, timezone)
    scheduled, undated = [], []
    for event in conventions:
        if region and not matches_region(event.region, region):
            continue
        if event.status == "cancelled" and not include_cancelled:
            continue
        begin, finish = instant(event.start), instant(event.end)
        if is_undated(event):
            undated.append(event)
        elif begin < end and finish >= start:
            scheduled.append(event)
    scheduled.sort(key=lambda e: (instant(e.start), e.id))
    undated.sort(key=lambda e: (e.name, e.id))
    return scheduled + undated, label


def name_query(conventions, name):
    name = name.strip()
    if not name or len(name) > 200:
        raise UserError("搜索名称须为 1–200 个字符。")
    events = [
        event
        for event in conventions
        if event.status != "cancelled" and name.casefold() in event.name.casefold()
    ]
    events.sort(
        key=lambda e: (is_undated(e), instant(e.start) or datetime.max.replace(tzinfo=UTC), e.id)
    )
    return events, f"名称包含“{name}” · 日期不限 · 默认隐藏取消"


@dataclass(frozen=True)
class Snapshot:
    code: str
    chat: str
    events: tuple
    label: str
    page_size: int
    created: float
    expires: float
    timezone: str = "Asia/Shanghai"
    layout: str = "compact"


class SnapshotStore:
    def __init__(self, path=":memory:"):
        self.command_entry = "FEC"
        self.connection = sqlite3.connect(path)
        self.prune_pending = False
        self.connection.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            code TEXT PRIMARY KEY, chat TEXT NOT NULL, user TEXT NOT NULL,
            payload TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL)""")
        # Separate pending markers preserve the historical snapshots schema;
        # existing records remain usable without inferring a new success state.
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_snapshots (code TEXT PRIMARY KEY)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS snapshots_expiry ON snapshots (expires, code)"
        )
        self.connection.execute("""CREATE TABLE IF NOT EXISTS replies (
            chat TEXT NOT NULL, message TEXT NOT NULL, code TEXT NOT NULL, page INTEGER NOT NULL,
            PRIMARY KEY(chat, message))""")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS page_views (
            chat TEXT NOT NULL, user TEXT NOT NULL, code TEXT NOT NULL, page INTEGER NOT NULL,
            PRIMARY KEY(chat, user, code))""")
        for table in ("replies", "page_views"):
            columns = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            if "layout" not in columns:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN layout TEXT NOT NULL DEFAULT 'compact'"
                )
        self.connection.commit()

    def create(
        self, chat, user, events, label, page_size, now, lifetime,
        timezone="Asia/Shanghai", layout="compact", *, ready=True,
    ):
        created = now.timestamp()
        payload = json.dumps(
            {
                "events": [asdict(e) for e in events],
                "label": label,
                "page_size": page_size,
                "timezone": timezone,
                "layout": layout,
            },
            ensure_ascii=False,
        )
        for _ in range(128):
            code = secrets.token_hex(3).upper()
            try:
                with self.connection:
                    self.connection.execute(
                        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
                        (code, chat, user, payload, created, created + lifetime),
                    )
                    if not ready:
                        self.connection.execute(
                            "INSERT INTO pending_snapshots VALUES (?)", (code,)
                        )
                return Snapshot(
                    code,
                    chat,
                    tuple(events),
                    label,
                    page_size,
                    created,
                    created + lifetime,
                    timezone,
                    layout,
                )
            except sqlite3.IntegrityError:
                continue
        raise UserError("查询短码分配失败，请稍后重新查询。")

    def get(self, code, chat, now):
        row = self.connection.execute(
            "SELECT payload, created, expires FROM snapshots WHERE code = ? AND chat = ?",
            (code.upper(), chat),
        ).fetchone()
        if row is None:
            raise UserError(f"此聊天没有该查询短码，请核对短码或重新 /{self.command_entry} 查询。")
        payload, created, expires = row
        if now.timestamp() >= expires:
            raise UserError(
                f"查询已过期，请重新 /{self.command_entry} 查询；重启不会延长原有效期。"
            )
        data = json.loads(payload)
        return Snapshot(
            code.upper(),
            chat,
            tuple(Convention(**e) for e in data["events"]),
            data["label"],
            data["page_size"],
            created,
            expires,
            data.get("timezone", "Asia/Shanghai"),
            data.get("layout", "compact"),
        )

    def close(self):
        self.connection.close()

    def prune(self, now, *, grace=86400, limit=100):
        cutoff = now.timestamp() - grace
        codes = [row[0] for row in self.connection.execute(
            "SELECT code FROM snapshots WHERE expires <= ? ORDER BY expires, code LIMIT ?",
            (cutoff, limit + 1),
        )]
        more = len(codes) > limit
        codes = codes[:limit]
        with self.connection:
            for code in codes:
                self.connection.execute("DELETE FROM replies WHERE code = ?", (code,))
                self.connection.execute("DELETE FROM page_views WHERE code = ?", (code,))
                self.connection.execute("DELETE FROM pending_snapshots WHERE code = ?", (code,))
                self.connection.execute("DELETE FROM snapshots WHERE code = ?", (code,))
        self.prune_pending = more
        return len(codes)

    def chats(self):
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT chat FROM snapshots GROUP BY chat ORDER BY MAX(created) DESC LIMIT 200"
            )
        ]

    def activate(self, code):
        """Expose a newly generated query only after its output is registered."""
        with self.connection:
            self.connection.execute("DELETE FROM pending_snapshots WHERE code = ?", (code,))

    def latest(self, chat, user, now):
        row = self.connection.execute(
            "SELECT code FROM snapshots WHERE chat = ? AND user = ? "
            "AND code NOT IN (SELECT code FROM pending_snapshots) "
            "ORDER BY created DESC, rowid DESC LIMIT 1",
            (chat, user),
        ).fetchone()
        if not row:
            raise UserError(
                f"你在此聊天没有可用的最近查询，请先 /{self.command_entry} 查询，或使用明确短码选择。"
            )
        return self.get(row[0], chat, now)

    def bind_message(self, chat, message, code, page, layout="compact"):
        existing = self.connection.execute(
            "SELECT code, page, layout FROM replies WHERE chat = ? AND message = ?", (chat, message)
        ).fetchone()
        if existing:
            return existing == (code, page, layout)
        with self.connection:
            self.connection.execute(
                "INSERT INTO replies (chat, message, code, page, layout) VALUES (?, ?, ?, ?, ?)",
                (chat, message, code, page, layout),
            )
        return True

    def referenced(self, chat, message, now):
        row = self.connection.execute(
            "SELECT code, page FROM replies WHERE chat = ? AND message = ?", (chat, message)
        ).fetchone()
        if not row:
            raise UserError(
                f"无法关联被引用的列表，请使用图片上的查询短码：/{self.command_entry} 选择 <短码> <序号> 或 /{self.command_entry} 翻页 <短码> <页码>。"
            )
        return self.get(row[0], chat, now), row[1]

    def current_page(self, chat, user, code):
        row = self.connection.execute(
            "SELECT page FROM page_views WHERE chat=? AND user=? AND code=?", (chat, user, code)
        ).fetchone()
        return row[0] if row else 1

    def current_layout(self, chat, user, code):
        row = self.connection.execute(
            "SELECT layout FROM page_views WHERE chat=? AND user=? AND code=?", (chat, user, code)
        ).fetchone()
        return row[0] if row else None

    def message_layout(self, chat, message):
        row = self.connection.execute(
            "SELECT layout FROM replies WHERE chat=? AND message=?", (chat, message)
        ).fetchone()
        return row[0] if row else None

    def remember_page(self, chat, user, code, page, layout="compact"):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO page_views (chat, user, code, page, layout) "
                "VALUES (?, ?, ?, ?, ?)", (chat, user, code, page, layout),
            )
