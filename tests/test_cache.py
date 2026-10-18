from dataclasses import replace
from datetime import datetime, timedelta

import httpx
from test_query import Renderer, row

from furrycons.app import Application
from furrycons.cache import CatalogCache
from furrycons.config import Settings
from furrycons.query import SnapshotStore
from furrycons.source import FurryConsSource


async def test_fresh_boundary_stale_boundary_reopen_and_detail_never_falls_back(tmp_path):
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    state = {"failure": False, "name": "原始名称"}
    settings = Settings.from_dict({"api_key": "test-key"})

    def upstream(request):
        if state["failure"]:
            return httpx.Response(503)
        if request.url.path.endswith("event/"):
            return httpx.Response(
                200,
                json={
                    "current": 1,
                    "pageSize": 100,
                    "total": 1,
                    "data": [row("x", name=state["name"])],
                },
            )
        return httpx.Response(200, json=row("x", name=state["name"]))

    def reopen(config=settings):
        return Application(
            config,
            FurryConsSource(config, httpx.MockTransport(upstream)),
            Renderer(),
            clock=lambda: now[0],
            snapshots=SnapshotStore(tmp_path / "snapshots.db"),
            cache=CatalogCache(tmp_path / "cache.db"),
        )

    app = reopen()
    await app.execute("查询")
    original_code = app.renderer.lists[-1]["code"]
    await app.close()
    app = reopen()
    try:
        state["name"] = "新的名称"
        now[0] += timedelta(minutes=10, microseconds=-1)
        await app.execute("查询")
        assert app.renderer.lists[-1]["events"][0]["name"] == "原始名称"
        assert "数据获取于" in app.renderer.lists[-1]["range"]
        now[0] += timedelta(microseconds=1)
        state["failure"] = True
        await app.execute("查询")
        assert "读取失败，使用旧缓存" in app.renderer.lists[-1]["range"]
        assert "2026-10-18 08:00:00" in app.renderer.lists[-1]["range"]
        assert "暂不可用" in (await app.execute(f"选择 {original_code} 1")).text
        now[0] = datetime.fromisoformat("2026-10-19T00:00:00Z")
        await app.execute("查询")
        assert "使用旧缓存" in app.renderer.lists[-1]["range"]
        now[0] += timedelta(microseconds=1)
        assert "暂不可用" in (await app.execute("查询")).text
    finally:
        await app.close()


async def test_refresh_changes_new_query_but_not_old_snapshot_and_source_keys_isolate(tmp_path):
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    state = {"rows": [row("x", name="旧名称")], "failure": False}
    settings = Settings.from_dict({"api_key": "first-key"})

    def upstream(_):
        if state["failure"]:
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={
                "current": 1,
                "pageSize": 100,
                "total": len(state["rows"]),
                "data": state["rows"],
            },
        )

    def reopen(config):
        return Application(
            config,
            FurryConsSource(config, httpx.MockTransport(upstream)),
            Renderer(),
            clock=lambda: now[0],
            snapshots=SnapshotStore(tmp_path / "state.db"),
            cache=CatalogCache(tmp_path / "cache.db"),
        )

    app = reopen(settings)
    await app.execute("查询", chat="group")
    code = app.renderer.lists[-1]["code"]
    state["rows"] = [row("x", name="新名称"), row("y")]
    now[0] += timedelta(minutes=10)
    await app.execute("查询", chat="group")
    assert app.renderer.lists[-1]["total"] == 2
    await app.execute(f"翻页 {code} 1", chat="group")
    assert app.renderer.lists[-1]["events"][0]["name"] == "旧名称"
    await app.close()
    state["failure"] = True
    for config in [
        replace(settings, api_key="different-key"),
        replace(settings, api_base="https://other.example/open"),
    ]:
        app = reopen(config)
        try:
            assert "暂不可用" in (await app.execute("查询")).text
        finally:
            await app.close()


async def test_cached_empty_catalog_during_failure_is_explicitly_stale():
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    state = [False]

    def upstream(_):
        return (
            httpx.Response(503)
            if state[0]
            else httpx.Response(200, json={"current": 1, "pageSize": 100, "total": 0, "data": []})
        )

    settings = Settings.from_dict({"api_key": "key"})
    app = Application(
        settings,
        FurryConsSource(settings, httpx.MockTransport(upstream)),
        Renderer(),
        clock=lambda: now[0],
    )
    try:
        assert "没有符合条件" in (await app.execute("查询")).text
        state[0] = True
        now[0] += timedelta(minutes=10)
        result = await app.execute("查询")
        assert "没有符合条件" in result.text and "使用旧缓存" in result.text
    finally:
        await app.close()
