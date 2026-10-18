from dataclasses import replace
from datetime import datetime, timedelta

import httpx
from test_query import Renderer, row

from furrycons.app import Application
from furrycons.config import Settings
from furrycons.query import SnapshotStore
from furrycons.source import FurryConsSource

UUID = "00000000-0000-4000-8000-000000000001"


def build(settings=None, path=":memory:", clock=None):
    settings = settings or Settings.from_dict({"api_key": "test-key"})

    def upstream(request):
        if request.url.path.endswith("event/"):
            return httpx.Response(
                200, json={"current": 1, "pageSize": 100, "total": 1, "data": [row(UUID)]}
            )
        return httpx.Response(200, json=row(request.url.path.rsplit("/", 1)[1]))

    return Application(
        settings,
        FurryConsSource(settings, httpx.MockTransport(upstream)),
        Renderer(),
        snapshots=SnapshotStore(path),
        clock=clock or (lambda: datetime.fromisoformat("2026-10-18T00:00:00Z")),
    )


async def test_default_disabled_and_switches_do_not_fall_through():
    app = build()
    try:
        for text in ["1", UUID, "展会"]:
            reply = await app.interact(text)
            assert not reply.text and not reply.image
        app.settings = replace(app.settings, bare_names=True)
        assert not (await app.interact("1")).image
        assert not (await app.interact(UUID)).image
        assert (await app.interact("展会")).image
        assert not app.renderer.details
        app.settings = replace(app.settings, bare_names=False, bare_ids=True)
        assert (await app.interact(UUID.upper())).image
        assert app.renderer.details[-1]["id"] == UUID
        assert not (await app.interact("展会")).image
        assert not (await app.interact("1")).image
        # Ordinary chat has no configuration mutation endpoint.
        await app.execute("开启裸数字")
        assert not app.settings.bare_numbers
    finally:
        await app.close()


async def test_recent_user_chat_context_survives_restart_and_never_falls_back(tmp_path):
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    settings = Settings.from_dict({"api_key": "key", "bare_numbers": True})
    app = build(settings, tmp_path / "state.db", lambda: now[0])
    await app.execute("查询", chat="a", user="alice")
    alice = app.renderer.lists[-1]["code"]
    await app.execute("查询", chat="b", user="alice")
    await app.execute("查询", chat="a", user="bob")
    await app.close()
    app = build(settings, tmp_path / "state.db", lambda: now[0])
    try:
        assert (await app.interact("1", chat="a", user="alice")).image
        assert "没有可用的最近查询" in (await app.interact("1", chat="a", user="charlie")).text
        assert "没有可用的最近查询" in (await app.interact("1", chat="c", user="alice")).text
        app.settings = replace(app.settings, bare_numbers=False)
        assert (await app.execute(f"选择 {alice} 1", chat="a", user="charlie")).image
        first = await app.execute("翻页 " + alice + " 1", chat="a")
        app.record_delivery(first, chat="a", message_id="quoted")
        assert (await app.interact("1", chat="a", user="charlie", quote_id="quoted")).image
        app.settings = replace(app.settings, bare_numbers=True)
        now[0] += timedelta(hours=24)
        assert "已过期" in (await app.interact("1", chat="a", user="alice")).text
    finally:
        await app.close()
