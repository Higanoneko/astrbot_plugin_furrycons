from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File
from astrbot.api.message_components import Reply as QuotedMessage
from astrbot.api.star import Context, Star, StarTools

from .furrycons.app import Application
from .furrycons.cache import CatalogCache
from .furrycons.config import Settings
from .furrycons.outputs import OutputStore
from .furrycons.query import SnapshotStore
from .furrycons.render import BrowserRenderer
from .furrycons.source import FurryConsSource
from .furrycons.summary import AstrBotSummaryModel


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
        data_dir = StarTools.get_data_dir("astrbot_plugin_furrycons")
        self.app = Application(
            settings,
            FurryConsSource(settings),
            BrowserRenderer(settings, data_dir / "images"),
            snapshots=SnapshotStore(data_dir / "state.sqlite3"),
            cache=CatalogCache(data_dir / "cache.sqlite3"),
            model=AstrBotSummaryModel(context),
            outputs=OutputStore(data_dir / "outputs.sqlite3", data_dir / "images"),
        )

    @filter.command("FEC")
    async def fec(self, event: AstrMessageEvent):
        """FurryCons：详情 <ID>、状态。"""
        # The command filter consumes the prefix but leaves message_str intact.
        text = event.message_str.strip()
        offset = text.find("FEC")
        command = text[offset + 3 :].strip() if offset >= 0 else ""
        async for result in self._respond(event, command):
            yield result

    @filter.command("查询展会")
    async def query_shortcut(self, event: AstrMessageEvent):
        """查询未来一年展会。"""
        tail = event.message_str.partition("查询展会")[2].strip()
        async for result in self._respond(event, "查询 " + tail):
            yield result

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
        reply = await self.app.execute(
            command, chat=chat, user=event.get_sender_id(), quote_id=self._quote_id(event)
        )
        async for result in self._send_reply(event, reply):
            yield result

    async def _send_reply(self, event, reply):
        messages = await self.app.deliver(reply, AstrBotTransport(event), chat=self._chat(event))
        for text in messages:
            yield event.plain_result(text)

    async def terminate(self):
        await self.app.close()
