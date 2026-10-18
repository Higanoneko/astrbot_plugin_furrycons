from datetime import datetime, timedelta

import httpx
from test_query import Renderer, row

from furrycons.app import Application
from furrycons.config import Settings
from furrycons.query import SnapshotStore
from furrycons.source import FurryConsSource


async def test_old_list_reference_multiple_users_pages_and_reopen(tmp_path):
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    rows = [row("a"), row("b"), row("c")]
    settings = Settings.from_dict({"api_key": "key", "cache_minutes": 0, "page_size": 1})

    def upstream(request):
        if request.url.path.endswith("event/"):
            return httpx.Response(
                200, json={"current": 1, "pageSize": 100, "total": len(rows), "data": rows}
            )
        identity = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json=row(identity, name="最新详情 " + identity))

    def reopen():
        return Application(
            settings,
            FurryConsSource(settings, httpx.MockTransport(upstream)),
            Renderer(),
            clock=lambda: now[0],
            snapshots=SnapshotStore(tmp_path / "state.db"),
        )

    app = reopen()
    first = await app.execute("查询", chat="g", user="alice")
    assert app.record_delivery(first, chat="g", message_id="original-message")
    second = await app.execute("下一页", chat="g", user="bob", quote_id="original-message")
    assert app.record_delivery(second, chat="g", message_id="second-page")
    assert app.renderer.lists[-1]["events"][0]["number"] == 2
    rows[:] = [row("new")]
    new = await app.execute("查询", chat="g", user="bob")
    assert not app.record_delivery(new, chat="g", message_id="original-message")
    await app.close()
    app = reopen()
    try:
        await app.execute("1", chat="g", user="bob", quote_id="original-message")
        assert app.renderer.details[-1]["id"] == "a"
        assert app.renderer.details[-1]["name"] == "最新详情 a"
        await app.execute("下一页", chat="g", user="alice", quote_id="second-page")
        assert app.renderer.lists[-1]["events"][0]["number"] == 3
        assert (
            "无法关联" in (await app.execute("1", chat="other", quote_id="original-message")).text
        )
        now[0] += timedelta(hours=24)
        assert "已过期" in (await app.execute("1", chat="g", quote_id="original-message")).text
    finally:
        await app.close()


async def test_missing_receipt_does_not_guess_the_latest_query():
    from test_query import app_for

    app = app_for([row("a")])
    try:
        reply = await app.execute("查询", chat="g")
        assert app.record_delivery(reply, chat="g", message_id=None) is False
        result = await app.execute("1", chat="g", quote_id="unbound")
        assert "无法关联" in result.text and "短码" in result.text and result.image is None
    finally:
        await app.close()
