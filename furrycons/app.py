import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import ceil
from pathlib import Path

from .cache import CatalogCache
from .delivery import deliver
from .errors import UserError
from .outputs import OutputStore
from .query import SnapshotStore, default_query, is_undated, name_query, query_options
from .summary import introduction_summary


@dataclass(frozen=True)
class Reply:
    text: str = ""
    image: Path | None = None
    query_code: str | None = None
    page: int = 0
    file: Path | None = None
    output_code: str | None = None


class Application:
    """Chat and future management entry points share the same decisions."""

    def __init__(
        self,
        settings,
        source,
        renderer,
        *,
        clock=None,
        snapshots=None,
        cache=None,
        model=None,
        outputs=None,
    ):
        self.settings, self.source, self.renderer = settings, source, renderer
        self.clock = clock or (lambda: datetime.now(UTC))
        self.snapshots = snapshots or SnapshotStore()
        self.cache = cache or CatalogCache()
        self.model = model
        self.outputs = outputs or OutputStore()

    async def execute(self, command: str, *, chat="local", user="local", quote_id=None):
        try:
            reply = await self._execute(command, chat=chat, user=user, quote_id=quote_id)
            if reply.image and reply.output_code is None:
                code = self.outputs.register(
                    reply.image, chat, self.clock(), self.settings.interaction_hours * 3600
                )
                reply = replace(
                    reply,
                    output_code=code,
                    text=(reply.text + "\n" if reply.text else "") + f"原文件：/FEC 原图 {code}",
                )
            return reply
        except UserError as exc:
            return Reply(text=str(exc))

    async def _execute(self, command: str, *, chat, user, quote_id):
        args = command.strip().split()
        single = "--single" in args
        args = [arg for arg in args if arg != "--single"]
        try:
            if quote_id is not None and (
                (len(args) == 1 and (args[0].isdigit() or args[0] in ("下一页", "上一页")))
                or (len(args) == 2 and args[0] in ("选择", "翻页"))
            ):
                snapshot, page = self.snapshots.referenced(chat, str(quote_id), self.clock())
                if args[0] in ("下一页", "上一页"):
                    return await self._page(snapshot, page + (1 if args[0] == "下一页" else -1))
                action, number = ("选择", args[0]) if len(args) == 1 else args
                return await self.execute(
                    f"{action} {snapshot.code} {number}", chat=chat, user=user
                )
            if args == ["状态"]:
                return Reply(text=await self.renderer.status())
            if len(args) == 2 and args[0] in ("原图", "原文件"):
                return Reply(file=self.outputs.get(args[1], chat, self.clock()))
            if args and args[0] == "查询":
                query_options(args[1:], self.clock(), self.settings.timezone)
                catalog, note = await self.cache.load(self.source, self.settings, self.clock)
                events, label = default_query(
                    catalog, self.clock(), self.settings.timezone, args[1:]
                )
                return await self._query_reply(
                    events, label + " · " + note, chat, user, single=single
                )
            if args and args[0] == "搜索":
                if len(args) < 2:
                    raise UserError("请提供名称：/FEC 搜索 <展会名称>。")
                catalog, note = await self.cache.load(self.source, self.settings, self.clock)
                events, label = name_query(catalog, " ".join(args[1:]))
                return await self._query_reply(
                    events, label + " · " + note, chat, user, single=single
                )
            if len(args) == 2 and args[0] == "单图":
                snapshot = self.snapshots.get(args[1], chat, self.clock())
                return await self._page(snapshot, 1, all_results=True)
            if len(args) == 3 and args[0] == "翻页":
                snapshot = self.snapshots.get(args[1], chat, self.clock())
                if len(args[2]) > 9 or not args[2].isascii() or not args[2].isdigit():
                    raise UserError("页码须为正整数。用法：/FEC 翻页 <短码> <页码>。")
                return await self._page(snapshot, int(args[2]))
            if len(args) == 3 and args[0] == "选择":
                snapshot = self.snapshots.get(args[1], chat, self.clock())
                if len(args[2]) > 9 or not args[2].isascii() or not args[2].isdigit():
                    raise UserError("序号须为列表上的正整数。用法：/FEC 选择 <短码> <序号>。")
                index = int(args[2])
                if not 1 <= index <= len(snapshot.events):
                    raise UserError(f"选择序号超出范围，请使用 1–{len(snapshot.events)}。")
                return await self._detail(snapshot.events[index - 1].id)
            if len(args) == 2 and args[0] == "详情":
                return await self._detail(args[1])
            return Reply(
                text="用法：/FEC 查询；/FEC 搜索 <名称>；/FEC 翻页 <短码> <页码>；"
                "/FEC 选择 <短码> <序号>；/FEC 详情 <ID>；/FEC 原图 <输出码>；/FEC 状态。"
            )
        except UserError as exc:
            return Reply(text=str(exc))

    async def _detail(self, identity):
        convention = await self.source.detail(identity)
        view = convention.detail_view(self.settings.timezone)
        summary = await introduction_summary(convention.description, self.settings, self.model)
        if summary is not None:
            view["introduction"] = summary
            view["introduction_type"] = "AI 总结"
        return Reply(image=await self.renderer.detail(view))

    async def interact(self, text, *, chat="local", user="local", quote_id=None):
        text = text.strip()
        if quote_id is not None:
            return await self.execute(text, chat=chat, user=user, quote_id=quote_id)
        # Classify before testing switches: disabled numeric/ID triggers cannot
        # accidentally fall through to name search when bare_names is enabled.
        if text.isdigit():
            if not self.settings.bare_numbers:
                return Reply()
            try:
                snapshot = self.snapshots.latest(chat, user, self.clock())
            except UserError as exc:
                return Reply(text=str(exc))
            return await self.execute(f"选择 {snapshot.code} {text}", chat=chat, user=user)
        if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", text):
            return (
                await self.execute("详情 " + text.lower(), chat=chat, user=user)
                if self.settings.bare_ids
                else Reply()
            )
        if self.settings.bare_names and text:
            return await self.execute("搜索 " + text, chat=chat, user=user)
        return Reply()

    async def _query_reply(self, events, label, chat, user, *, single=False):
        if not events:
            return Reply(text=f"查询范围：{label}。没有符合条件的展会。数据来源：FurryCons。")
        snapshot = self.snapshots.create(
            chat,
            user,
            events,
            label,
            len(events) if single else self.settings.page_size,
            self.clock(),
            self.settings.interaction_hours * 3600,
            self.settings.timezone,
        )
        return await self._page(snapshot, 1, all_results=single)

    async def _page(self, snapshot, page, *, all_results=False):
        page_size = len(snapshot.events) if all_results else snapshot.page_size
        pages = ceil(len(snapshot.events) / page_size)
        if not 1 <= page <= pages:
            raise UserError(f"页码超出范围，请使用 1–{pages} 页。")
        offset = (page - 1) * page_size
        events = []
        for index, event in enumerate(snapshot.events[offset : offset + page_size], offset + 1):
            view = event.detail_view(snapshot.timezone)
            view["number"] = index
            if is_undated(event):
                view["schedule_note"] = "日期待定（不归入上述日程范围）"
            else:
                view["schedule_note"] = ""
            events.append(view)
        data = {
            "events": events,
            "range": ("全部结果单图 · " if all_results else "") + snapshot.label,
            "code": snapshot.code,
            "page": page,
            "pages": pages,
            "total": len(snapshot.events),
        }
        path = await self.renderer.listing(data)
        return Reply(
            text=f"查询短码 {snapshot.code} · 第 {page}/{pages} 页 · 共 {len(snapshot.events)} 场。\n"
            f"翻页：/FEC 翻页 {snapshot.code} <页码>\n选择：/FEC 选择 {snapshot.code} <序号>",
            image=path,
            query_code=snapshot.code,
            page=page,
        )

    def record_delivery(self, reply, *, chat, message_id):
        if reply.query_code is None or message_id is None or not str(message_id).strip():
            return False
        return self.snapshots.bind_message(chat, str(message_id), reply.query_code, reply.page)

    async def deliver(self, reply, transport, *, chat):
        def record(receipt):
            message_id = receipt.get("message_id") if isinstance(receipt, dict) else receipt
            if isinstance(message_id, (str, int)) and not isinstance(message_id, bool):
                self.record_delivery(reply, chat=chat, message_id=message_id)

        return await deliver(reply, transport, record)

    async def close(self):
        try:
            await self.source.close()
        finally:
            try:
                await self.renderer.close()
            finally:
                self.cache.close()
                self.snapshots.close()
                self.outputs.close()
