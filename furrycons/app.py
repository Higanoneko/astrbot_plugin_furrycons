import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import ceil
from pathlib import Path

from .cache import CatalogCache
from .commands import CommandRoutes
from .delivery import deliver
from .errors import UserError
from .outputs import OutputStore
from .preview import preview_data
from .query import SnapshotStore, default_query, is_undated, name_query, query_options
from .summary import introduction_summary
from .themes import ThemeStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reply:
    text: str = ""
    image: Path | None = None
    query_code: str | None = None
    page: int = 0
    file: Path | None = None
    output_code: str | None = None
    layout: str = "compact"
    new_query: bool = False


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
        themes=None,
    ):
        self.settings, self.source, self.renderer = settings, source, renderer
        self.clock = clock or (lambda: datetime.now(UTC))
        self.snapshots = snapshots or SnapshotStore()
        self.cache = cache or CatalogCache()
        self.model = model
        self.outputs = outputs or OutputStore()
        self.themes = themes or ThemeStore()
        self._last_maintenance = None
        self._maintenance_pending = False
        self._sending_paths = {}
        self.snapshots.command_entry = self.outputs.command_entry = self.themes.command_entry = (
            settings.command_entry
        )

    @property
    def entry(self):
        return "/" + self.settings.command_entry

    async def dispatch(self, text, **context):
        command = CommandRoutes(self.settings).parse(text)
        return await self.execute(command, **context) if command is not None else Reply()

    async def execute(
        self,
        command: str,
        *,
        chat="local",
        user="local",
        quote_id=None,
        is_admin=False,
        attachment=None,
    ):
        self._maintain()
        try:
            reply = await self._execute(
                command,
                chat=chat,
                user=user,
                quote_id=quote_id,
                is_admin=is_admin,
                attachment=attachment,
            )
            if reply.image and reply.output_code is None:
                code = self.outputs.register(
                    reply.image, chat, self.clock(), self.settings.interaction_hours * 3600
                )
                reply = replace(
                    reply,
                    output_code=code,
                    text=(reply.text + "\n" if reply.text else "")
                    + f"原文件：{self.entry} 原图 {code}",
                )
            if reply.image and reply.query_code:
                self.snapshots.remember_page(chat, user, reply.query_code, reply.page, reply.layout)
                if reply.new_query:
                    self.snapshots.activate(reply.query_code)
            return reply
        except UserError as exc:
            return Reply(text=str(exc))

    def _maintain(self):
        now = self.clock()
        timestamp = now.timestamp()
        if (not self._maintenance_pending and self._last_maintenance is not None
                and 0 <= timestamp - self._last_maintenance < 3600):
            return
        self._last_maintenance = timestamp
        pending = False
        for store in (self.snapshots, self.outputs):
            try:
                if store is self.outputs:
                    store.prune(now, protected=self._sending_paths)
                else:
                    store.prune(now)
                pending = pending or store.prune_pending
            except (OSError, sqlite3.Error):
                logger.warning("过期记录维护失败，稍后重试。", exc_info=True)
        self._maintenance_pending = pending

    async def _execute(self, command: str, *, chat, user, quote_id, is_admin, attachment):
        args = command.strip().split()
        if not args or args == ["帮助"]:
            return Reply(text=self._help())
        single = "--single" in args
        args = [arg for arg in args if arg != "--single"]
        try:
            if args == ["主题导入"]:
                theme = await self.manage_theme("import", is_admin=is_admin, path=attachment)
                return Reply(
                    text=f"已安装 {theme.key} · {theme.manifest['name']}。默认主题未改变；单次使用：--theme {theme.key}"
                )
            if args == ["主题列表"]:
                return Reply(text="已安装主题：\n" + "\n".join(self.themes.installed()))
            if len(args) == 2 and args[0] == "主题更新":
                theme = await self.manage_theme(
                    "update", key=args[1], path=attachment, is_admin=is_admin
                )
                return Reply(text=f"已更新 {theme.key}；默认值与独立配色保留，旧包已备份。")
            if len(args) == 2 and args[0] == "主题配色":
                theme = await self.manage_theme("palette", key=args[1])
                return Reply(
                    text=f"{theme['key']} · 明暗 {theme['mode']}\n"
                    + "\n".join(f"{key}：{value}" for key, value in theme["colors"].items())
                )
            if args and args[0] == "主题改色":
                if len(args) == 3 and args[2] == "重置":
                    theme = await self.manage_theme("reset_colors", is_admin=is_admin, key=args[1])
                elif len(args) == 4:
                    field, value = args[2:]
                    changes = (
                        {"primary": value}
                        if field == "主色"
                        else {"mode": value}
                        if field == "明暗"
                        else {"advanced": {field: value}}
                    )
                    theme = await self.manage_theme(
                        "set_colors", is_admin=is_admin, key=args[1], changes=changes
                    )
                else:
                    raise UserError(
                        "用法：主题改色 <标识> 主色 <#RRGGBB>；明暗 <light|dark>；<高级颜色项> <#RRGGBB>；重置。"
                    )
                return Reply(
                    text=f"已保存 {theme['key']} 的独立配色；明暗 {theme['mode']}，主色 {theme['colors']['primary']}。"
                )
            if len(args) >= 3 and args[0] == "主题副本":
                theme = await self.manage_theme(
                    "copy",
                    is_admin=is_admin,
                    key=args[1],
                    identity=args[2],
                    name=" ".join(args[3:]) or None,
                )
                return Reply(
                    text=f"已保存本地副本 {theme.key}（来源 {args[1]}），原主题未改变。布局请导出 ZIP 编辑后以新标识导入。"
                )
            if len(args) == 2 and args[0] == "主题导出":
                return Reply(file=await self.manage_theme("export", is_admin=is_admin, key=args[1]))
            if args and args[0] == "主题默认":
                if len(args) == 1:
                    defaults = await self.manage_theme("defaults", chat=chat)
                    return Reply(
                        text=f"全局默认：{defaults['global'] or '未设置（Material）'}\n"
                        f"本会话默认：{defaults['session'] or '未设置（继承全局）'}\n"
                        f"当前生效：{defaults['effective']}"
                    )
                if len(args) == 3 and args[1] in ("全局", "本会话"):
                    await self.manage_theme(
                        "set_default",
                        chat=chat,
                        is_admin=is_admin,
                        scope="global" if args[1] == "全局" else "session",
                        key=None if args[2] == "清除" else args[2],
                    )
                    return Reply(
                        text=f"已更新{args[1]}默认主题；当前生效：{self.themes.defaults(chat)['effective']}。"
                    )
                raise UserError(
                    f"用法：{self.entry} 主题默认 [全局|本会话] <主题标识|清除>；不带参数查看当前默认值。"
                )
            explicit_theme = None
            explicit_layout = None
            quoted_layout = None
            if "--layout" in args:
                index = args.index("--layout")
                if args.count("--layout") != 1 or index + 1 >= len(args):
                    raise UserError("列表排版用法：--layout compact|poster|grid。")
                explicit_layout = args[index + 1]
                if explicit_layout not in ("compact", "poster", "grid"):
                    raise UserError("列表排版须为 compact（紧凑图文）、poster（大图海报）或 grid（双列目录）。")
                args = args[:index] + args[index + 2 :]
                if not args or args[0] not in ("查询", "搜索", "翻页", "单图", "下一页", "上一页"):
                    raise UserError("--layout 仅用于查询、搜索、翻页和全部结果单图。")
            if "--theme" in args:
                index = args.index("--theme")
                if args.count("--theme") != 1 or index + 1 >= len(args):
                    raise UserError(
                        f"单次主题用法：--theme <已安装主题标识>；可用 {self.entry} 主题列表 查看。"
                    )
                explicit_theme = self.themes.get(args[index + 1]).key
                args = args[:index] + args[index + 2 :]
            theme_key = self.themes.defaults(chat)["effective"]
            if args and (
                args[0] in ("查询", "搜索", "详情", "选择", "翻页", "单图", "下一页", "上一页")
                or args[0].isdigit()
            ):
                theme_key = self.themes.select_key(chat, explicit_theme)
            if quote_id is not None and (
                (len(args) == 1 and (args[0].isdigit() or args[0] in ("下一页", "上一页")))
                or (len(args) == 2 and args[0] in ("选择", "翻页"))
            ):
                snapshot, page = self.snapshots.referenced(chat, str(quote_id), self.clock())
                quoted_layout = self.snapshots.message_layout(chat, str(quote_id))
                if args[0] in ("下一页", "上一页"):
                    return await self._page(
                        snapshot, page + (1 if args[0] == "下一页" else -1), theme_key=theme_key,
                        user=user, layout=explicit_layout, quoted_layout=quoted_layout,
                    )
                action, number = ("选择", args[0]) if len(args) == 1 else args
                args = [action, snapshot.code, number]
            elif (
                (len(args) == 1 and args[0] in ("下一页", "上一页"))
                or (len(args) == 2 and args[0] in ("选择", "翻页"))
            ):
                snapshot = self.snapshots.latest(chat, user, self.clock())
                args = [args[0], snapshot.code, *args[1:]]
            if args == ["状态"]:
                return Reply(text=await self.renderer.status())
            if len(args) == 2 and args[0] in ("原图", "原文件"):
                return Reply(file=self.outputs.get(args[1], chat, self.clock()))
            if args and args[0] == "查询":
                query_options(args[1:], self.clock(), self.settings.timezone)
                catalog, note = await self.cache.load(
                    self.source, self.settings, self.clock, refresh=True
                )
                events, label = default_query(
                    catalog, self.clock(), self.settings.timezone, args[1:]
                )
                return await self._query_reply(
                    events, label + " · " + note, chat, user, single=single, theme_key=theme_key,
                    layout=explicit_layout,
                )
            if args and args[0] == "搜索":
                if len(args) < 2:
                    raise UserError(f"请提供名称：{self.entry} 搜索 <展会名称>。")
                catalog, note = await self.cache.load(
                    self.source, self.settings, self.clock, refresh=True
                )
                events, label = name_query(catalog, " ".join(args[1:]))
                return await self._query_reply(
                    events, label + " · " + note, chat, user, single=single, theme_key=theme_key,
                    layout=explicit_layout,
                )
            if len(args) == 2 and args[0] == "单图":
                snapshot = self.snapshots.get(args[1], chat, self.clock())
                return await self._page(
                    snapshot, 1, all_results=True, theme_key=theme_key, user=user,
                    layout=explicit_layout,
                )
            if len(args) == 2 and args[0] in ("下一页", "上一页"):
                snapshot = self.snapshots.get(args[1], chat, self.clock())
                page = self.snapshots.current_page(chat, user, snapshot.code)
                return await self._page(
                    snapshot, page + (1 if args[0] == "下一页" else -1), theme_key=theme_key,
                    user=user, layout=explicit_layout,
                )
            if len(args) == 3 and args[0] == "翻页":
                snapshot = self.snapshots.get(args[1], chat, self.clock())
                if len(args[2]) > 9 or not args[2].isascii() or not args[2].isdigit():
                    raise UserError(f"页码须为正整数。用法：{self.entry} 翻页 <短码> <页码>。")
                return await self._page(
                    snapshot, int(args[2]), theme_key=theme_key, user=user,
                    layout=explicit_layout, quoted_layout=quoted_layout,
                )
            if len(args) == 3 and args[0] == "选择":
                snapshot = self.snapshots.get(args[1], chat, self.clock())
                if len(args[2]) > 9 or not args[2].isascii() or not args[2].isdigit():
                    raise UserError(
                        f"序号须为列表上的正整数。用法：{self.entry} 选择 <短码> <序号>。"
                    )
                index = int(args[2])
                if not 1 <= index <= len(snapshot.events):
                    raise UserError(f"选择序号超出范围，请使用 1–{len(snapshot.events)}。")
                return await self._detail(snapshot.events[index - 1].id, theme_key)
            if len(args) == 2 and args[0] == "详情":
                return await self._detail(args[1], theme_key)
            usages = {
                "选择": "选择 <序号>，或 选择 <短码> <序号>",
                "翻页": "翻页 <页码>，或 翻页 <短码> <页码>",
                "下一页": "下一页 [短码]",
                "上一页": "上一页 [短码]",
                "详情": "详情 <展会ID>；也可先查询，再使用选择命令",
                "单图": "单图 <查询短码>",
                "原图": "原图 <输出码>；输出码见图片附带的原文件命令",
                "原文件": "原文件 <输出码>；输出码见图片附带的原文件命令",
            }
            if args and args[0] in usages:
                return Reply(text=f"用法：{self.entry} {usages[args[0]]}。")
            return Reply(
                text=f"无法识别命令或参数，请发送 {self.entry} 帮助 查看用法。"
            )
        except UserError as exc:
            return Reply(text=str(exc))

    def _help(self):
        entry = self.entry
        return (
            "FurryCons 展会查询\n"
            f"① 查找：{entry} 查询，或 {entry} 搜索 <名称>\n"
            f"② 看详情：{entry} 选择 <短码> <序号>\n"
            f"③ 翻页：{entry} 下一页 <短码>，或 {entry} 翻页 <短码> <页码>\n"
            f"本人最近查询可简写：{entry} 选择 1、{entry} 下一页、{entry} 上一页、{entry} 翻页 2\n"
            "简写仅使用本人在本聊天最近成功生成的列表；操作旧列表或他人的列表请带短码，或引用原列表。\n"
            f"筛选示例：{entry} 查询 广东 30天\n"
            f"排版示例：{entry} 查询 --layout grid（也可选 compact、poster）\n"
            f"查看主题：{entry} 主题列表；单次使用：{entry} 查询 --theme <主题标识>\n"
            f"原文件：{entry} 原图 <输出码>；检查渲染环境：{entry} 状态\n"
            f"进阶：{entry} 详情 <展会ID>；{entry} 单图 <查询短码>\n"
            "短码和序号见列表图及其附带的文字；命令前缀以机器人配置为准。"
        )

    async def _detail(self, identity, theme_key):
        convention = await self.source.detail(identity)
        view = convention.detail_view(self.settings.timezone)
        view["theme_key"] = theme_key
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

    def _list_layout(self, theme_key, layout, *, explicit=False):
        supported = self.themes.get(theme_key).manifest.get("layouts", [])
        if explicit and layout not in supported:
            raise UserError("所选主题不支持此排版，请选择支持该排版的主题或省略 --layout。")
        return layout if layout in supported else supported[0] if supported else "compact"

    async def _query_reply(
        self, events, label, chat, user, *, single=False, theme_key, layout=None,
    ):
        selected_layout = self._list_layout(
            theme_key, layout or self.settings.list_layout, explicit=layout is not None
        )
        if not events:
            return Reply(
                text=f"查询范围：{label}。没有符合条件的展会。数据来源：FurryCons。\n"
                f"可尝试调整地区或日期，或使用 {self.entry} 搜索 <名称> 查找。"
            )
        snapshot = self.snapshots.create(
            chat,
            user,
            events,
            label,
            len(events) if single else self.settings.page_size,
            self.clock(),
            self.settings.interaction_hours * 3600,
            self.settings.timezone,
            selected_layout,
            ready=False,
        )
        reply = await self._page(
            snapshot, 1, all_results=single, theme_key=theme_key, user=user,
            quoted_layout=selected_layout,
        )
        return replace(reply, new_query=True)

    async def _page(
        self, snapshot, page, *, all_results=False, theme_key="builtin:material",
        user="local", layout=None, quoted_layout=None,
    ):
        selected_layout = self._list_layout(
            theme_key,
            layout or quoted_layout
            or self.snapshots.current_layout(snapshot.chat, user, snapshot.code) or snapshot.layout,
            explicit=layout is not None,
        )
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
            "command_entry": self.settings.command_entry,
            "theme_key": theme_key,
            "layout": selected_layout,
            "events": events,
            "range": ("全部结果单图 · " if all_results else "") + snapshot.label,
            "code": snapshot.code,
            "page": page,
            "pages": pages,
            "total": len(snapshot.events),
        }
        path = await self.renderer.listing(data)
        instructions = []
        if not all_results and page > 1:
            instructions.append(f"上一页：{self.entry} 上一页 {snapshot.code}")
        if not all_results and page < pages:
            instructions.append(f"下一页：{self.entry} 下一页 {snapshot.code}")
        instructions.append(
            f"看详情：{self.entry} 选择 {snapshot.code} {offset + 1}"
            f"（示例；本页序号 {offset + 1}–{offset + len(events)}）"
        )
        return Reply(
            text=f"查询短码 {snapshot.code} · 第 {page}/{pages} 页 · 共 {len(snapshot.events)} 场。\n"
            + "\n".join(instructions),
            image=path,
            query_code=snapshot.code,
            page=page,
            layout=selected_layout,
        )

    def record_delivery(self, reply, *, chat, message_id):
        if reply.query_code is None or message_id is None or not str(message_id).strip():
            return False
        return self.snapshots.bind_message(
            chat, str(message_id), reply.query_code, reply.page, reply.layout
        )

    async def manage_theme(self, action, *, chat="local", is_admin=False, **options):
        """The same permission and state decisions serve chat and WebUI callers."""
        if action == "defaults":
            return self.themes.defaults(chat)
        if action == "palette":
            return self.themes.get(options.get("key")).view()
        if action == "list":
            return self.themes.installed()
        if action == "catalog":
            return self.themes.catalog()
        if not is_admin:
            raise UserError("仅机器人管理员可以修改主题；群主或频道管理员身份不授予此权限。")
        if action == "set_default":
            if options.get("scope") not in ("global", "session") or "key" not in options:
                raise UserError("默认主题范围须为 global 或 session。")
            self.themes.set_default(
                options.get("key"), chat=None if options["scope"] == "global" else chat
            )
            return self.themes.defaults(chat)
        if action == "import":
            if not options.get("path"):
                raise UserError(
                    f"请上传主题 ZIP 并回复 {self.entry} 主题导入；此接入须能提供引用文件内容，也可通过 WebUI 导入。"
                )
            return self.themes.import_zip(options["path"])
        if action == "update":
            if not options.get("path"):
                raise UserError("请上传本地主题 ZIP，并明确指定要更新的 local: 标识。")
            return self.themes.update_zip(options.get("key"), options["path"])
        if action == "preview":
            key = self.themes.get(options.get("key")).key
            data = preview_data(key, self.settings.command_entry)
            if options.get("kind") == "list":
                data["query"]["layout"] = self._list_layout(
                    key, options.get("layout") or self.settings.list_layout,
                    explicit=options.get("layout") is not None,
                )
                return await self.renderer.listing(data["query"])
            if options.get("kind") == "detail":
                return await self.renderer.detail(data["event"])
            raise UserError("主题预览类型须为 list 或 detail。")
        if action == "set_colors":
            return self.themes.set_colors(options.get("key"), options.get("changes"))
        if action == "reset_colors":
            return self.themes.reset_colors(options.get("key"))
        if action == "copy":
            return self.themes.copy_theme(
                options.get("key"), options.get("identity"), options.get("name")
            )
        if action == "export":
            return self.themes.export_zip(options.get("key"))
        raise UserError("未知主题管理操作。")

    async def deliver(self, reply, transport, *, chat):
        def record(receipt):
            message_id = receipt.get("message_id") if isinstance(receipt, dict) else receipt
            if isinstance(message_id, (str, int)) and not isinstance(message_id, bool):
                self.record_delivery(reply, chat=chat, message_id=message_id)

        paths = {str(Path(path).resolve()) for path in (reply.image, reply.file) if path}
        for path in paths:
            self._sending_paths[path] = self._sending_paths.get(path, 0) + 1
        try:
            return await deliver(reply, transport, record)
        finally:
            for path in paths:
                self._sending_paths[path] -= 1
                if not self._sending_paths[path]:
                    del self._sending_paths[path]

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
                self.themes.close()
