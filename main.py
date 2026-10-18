import json
from hashlib import sha256
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File
from astrbot.api.message_components import Reply as QuotedMessage
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import star_handlers_registry

from .furrycons.admin import AdminSettings
from .furrycons.app import Application
from .furrycons.cache import CatalogCache
from .furrycons.commands import CommandRoutes
from .furrycons.config import Settings
from .furrycons.deployment import BrowserDeployment
from .furrycons.outputs import OutputStore
from .furrycons.query import SnapshotStore
from .furrycons.render import BrowserRenderer
from .furrycons.source import FurryConsSource
from .furrycons.summary import AstrBotSummaryModel
from .furrycons.theme_web import ThemeWeb
from .furrycons.themes import ThemeStore
from .furrycons.web import AdminWeb


def occupied_commands(module):
    occupied = set()
    for handler in star_handlers_registry:
        if handler.handler_module_path == module or not handler.enabled:
            continue
        plugin = star_map.get(handler.handler_module_path)
        if plugin is not None and not plugin.activated:
            continue
        for command_filter in handler.event_filters:
            if isinstance(command_filter, (CommandFilter, CommandGroupFilter)):
                occupied.update(command_filter.get_complete_command_names())
    return occupied


def command_handler(entry, action):
    async def handler(plugin, event):
        if entry in occupied_commands(type(plugin).__module__):
            yield event.plain_result(
                f"命令入口 {entry} 与其他插件冲突，请机器人管理者修改入口配置后重载。"
            )
            event.stop_event()
            return
        parts = event.message_str.strip().split(maxsplit=1)
        command = (action + " " + (parts[1] if len(parts) == 2 else "")).strip()
        async for result in plugin._respond(event, command):
            yield result

    handler.__name__ = (
        "fec"
        if not action
        else "query_shortcut"
        if entry == "查询展会"
        else "shortcut_" + sha256(entry.encode()).hexdigest()[:16]
    )
    handler.__doc__ = "FurryCons 主入口" if not action else "FurryCons 功能快捷入口：" + action
    return handler


class AstrBotTransport:
    def __init__(self, event):
        self.event = event
        # These framework adapters implement File. Backend acceptance is still
        # determined by the real send, and verified separately per integration.
        self.can_send_files = event.get_platform_name() in ("webchat", "telegram", "aiocqhttp")

    async def send_image(self, path):
        return await self.event.send(self.event.image_result(str(path)))

    async def send_file(self, path):
        return await self.event.send(
            self.event.chain_result([File(name=path.name, file=str(path))])
        )


class FurryConsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        settings = Settings.from_dict(dict(config))
        self.command_routes = CommandRoutes(settings)
        self.command_routes.validate_occupied(occupied_commands(type(self).__module__))
        data_dir = StarTools.get_data_dir("astrbot_plugin_furrycons")
        themes = ThemeStore(data_dir)
        self.app = Application(
            settings,
            FurryConsSource(settings),
            BrowserRenderer(settings, data_dir / "images", themes),
            snapshots=SnapshotStore(data_dir / "state.sqlite3"),
            cache=CatalogCache(data_dir / "cache.sqlite3"),
            model=AstrBotSummaryModel(context),
            outputs=OutputStore(data_dir / "outputs.sqlite3", data_dir / "images"),
            themes=themes,
        )
        # Use the supported decorator, after config is known and before the host
        # binds module handlers to this instance. The host owns reload cleanup.
        for entry, action in self.command_routes.bindings.items():
            filter.command(entry)(command_handler(entry, action))
        self.admin_settings = AdminSettings(
            config,
            settings,
            config.save_config_async,
            lambda: occupied_commands(type(self).__module__),
        )
        self.deployment = BrowserDeployment(data_dir, self.app.renderer, lambda: dict(config))
        self.web = AdminWeb(
            context,
            self.admin_settings,
            json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8")),
            self.deployment,
        )
        self.theme_web = ThemeWeb(context, self.app, data_dir)

    @filter.on_astrbot_loaded()
    async def check_loaded_commands(self):
        conflicts = sorted(
            set(self.command_routes.bindings).intersection(occupied_commands(type(self).__module__))
        )
        if conflicts:
            logger.warning("FurryCons 命令入口冲突：%s；请修改插件入口配置。", "、".join(conflicts))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def incoming_interaction(self, event: AstrMessageEvent):
        """引用交互与独立启用的裸触发。"""
        text = event.message_str.strip()
        parts = text.split()
        if not parts or event.get_extra("handlers_parsed_params"):
            return
        quote_id = self._quote_id(event)
        if quote_id is not None and not (
            text.isdigit()
            or text in ("下一页", "上一页")
            or (len(parts) == 2 and parts[0] in ("选择", "翻页"))
        ):
            return
        reply = await self.app.interact(
            text, chat=self._chat(event), user=event.get_sender_id(), quote_id=quote_id
        )
        if not reply.image and not reply.text:
            return
        async for result in self._send_reply(event, reply):
            yield result
        event.stop_event()

    @staticmethod
    def _quote_id(event):
        quotes = [
            str(component.id)
            for component in event.get_messages()
            if isinstance(component, QuotedMessage)
        ]
        return quotes[0] if len(quotes) == 1 else ("ambiguous-reference" if quotes else None)

    @staticmethod
    def _chat(event):
        # A per-user framework LLM session must not split a shared group query.
        origin = (
            event.get_group_id()
            or getattr(event.message_obj, "session_id", None)
            or event.get_session_id()
        )
        return f"{event.get_platform_id()}:{event.get_message_type().value}:{origin}"

    async def _respond(self, event, command):
        chat = self._chat(event)
        attachment = None
        if (
            command.strip() == "主题导入" or command.strip().startswith("主题更新 ")
        ) and event.is_admin():
            quotes = [m for m in event.get_messages() if isinstance(m, QuotedMessage)]
            components = (
                quotes[0].chain if len(quotes) == 1 else event.get_messages() if not quotes else []
            )
            files = [m for m in (components or []) if isinstance(m, File)]
            if len(files) == 1 and (files[0].name or "").lower().endswith(".zip"):
                try:
                    attachment = await files[0].get_file()
                except Exception:  # noqa: BLE001 - platform download errors
                    yield event.plain_result(
                        "无法获取主题 ZIP，请重新上传并回复导入，或使用 WebUI 导入。"
                    )
                    return
        reply = await self.app.execute(
            command,
            chat=chat,
            user=event.get_sender_id(),
            quote_id=self._quote_id(event),
            is_admin=event.is_admin(),
            attachment=attachment,
        )
        async for result in self._send_reply(event, reply):
            yield result

    async def _send_reply(self, event, reply):
        messages = await self.app.deliver(reply, AstrBotTransport(event), chat=self._chat(event))
        for text in messages:
            yield event.plain_result(text)

    async def terminate(self):
        await self.deployment.close()
        await self.app.close()
