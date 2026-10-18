from datetime import datetime, timedelta

import httpx
from test_query import Renderer, row

from furrycons.app import Application
from furrycons.config import Settings
from furrycons.query import SnapshotStore
from furrycons.source import FurryConsSource


async def test_reopen_selection_uses_original_identity_latest_detail_and_expiry(tmp_path):
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    rows = [row("a"), row("b")]
    details = {"a": row("a", name="新名称", detail="最新介绍"), "b": row("b")}
    fail_detail = [False]

    def upstream(request):
        if request.url.path.endswith("event/"):
            return httpx.Response(
                200, json={"current": 1, "pageSize": 100, "total": len(rows), "data": rows}
            )
        if fail_detail[0]:
            return httpx.Response(503)
        return httpx.Response(200, json=details[request.url.path.rsplit("/", 1)[1]])

    def reopen():
        settings = Settings.from_dict(
            {"api_key": "test-key", "interaction_hours": 2, "page_size": 1}
        )
        return Application(
            settings,
            FurryConsSource(settings, httpx.MockTransport(upstream)),
            Renderer(),
            clock=lambda: now[0],
            snapshots=SnapshotStore(tmp_path / "state.db"),
        )

    app = reopen()
    await app.execute("查询", chat="one", user="a")
    code = app.renderer.lists[-1]["code"]
    await app.execute("查询", chat="two", user="a")
    other = app.renderer.lists[-1]["code"]
    await app.close()
    rows.reverse()
    app = reopen()
    try:
        await app.execute(f"选择 {code} 1", chat="one", user="b")
        assert app.renderer.details[-1]["id"] == "a"
        assert app.renderer.details[-1]["name"] == "新名称"
        assert app.renderer.details[-1]["introduction"] == "最新介绍"
        assert "没有该查询" in (await app.execute(f"选择 {other} 1", chat="one")).text
        assert "超出范围" in (await app.execute(f"选择 {code} 0", chat="one")).text
        assert "正整数" in (await app.execute(f"选择 {code} x", chat="one")).text
        fail_detail[0] = True
        assert "暂不可用" in (await app.execute(f"选择 {code} 1", chat="one")).text
        now[0] += timedelta(hours=2, microseconds=-1)
        await app.execute(f"翻页 {code} 2", chat="one")
        assert app.renderer.lists[-1]["events"][0]["id"] == "b"
        now[0] += timedelta(microseconds=1)
        assert "已过期" in (await app.execute(f"翻页 {code} 1", chat="one")).text
    finally:
        await app.close()
    app = reopen()
    try:
        assert "已过期" in (await app.execute(f"选择 {code} 1", chat="one")).text
    finally:
        await app.close()


async def test_collision_after_restart_cannot_replace_existing_query(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-10-18T00:00:00Z")
    identities = iter(["abcdef", "abcdef", "123456"])
    monkeypatch.setattr("furrycons.query.secrets.token_hex", lambda _: next(identities))
    settings = Settings.from_dict({"api_key": "test-key"})
    current = [row("original")]

    def upstream(_):
        return httpx.Response(
            200, json={"current": 1, "pageSize": 100, "total": 1, "data": current}
        )

    def reopen():
        return Application(
            settings,
            FurryConsSource(settings, httpx.MockTransport(upstream)),
            Renderer(),
            clock=lambda: now,
            snapshots=SnapshotStore(tmp_path / "snapshots.db"),
        )

    app = reopen()
    await app.execute("查询", chat="chat")
    await app.close()
    current[:] = [row("replacement")]
    app = reopen()
    try:
        await app.execute("查询", chat="chat")
        assert app.renderer.lists[-1]["code"] == "123456"
        await app.execute("翻页 ABCDEF 1", chat="chat")
        assert app.renderer.lists[-1]["events"][0]["id"] == "original"
    finally:
        await app.close()
