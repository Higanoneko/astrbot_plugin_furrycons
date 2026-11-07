import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest
from test_query import Renderer, row

from furrycons.app import Application
from furrycons.cache import CatalogCache
from furrycons.config import Settings
from furrycons.query import SnapshotStore
from furrycons.source import FurryConsSource


async def test_new_queries_refresh_and_stale_boundary_reopen_detail_never_falls_back(tmp_path):
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
        assert app.renderer.lists[-1]["events"][0]["name"] == "新的名称"
        refreshed = now[0]
        assert "数据获取于" in app.renderer.lists[-1]["range"]
        now[0] += timedelta(microseconds=1)
        state["failure"] = True
        await app.execute("查询")
        assert "读取失败，使用旧缓存" in app.renderer.lists[-1]["range"]
        assert "2026-10-18 08:09:59" in app.renderer.lists[-1]["range"]
        assert "暂不可用" in (await app.execute(f"选择 {original_code} 1")).text
        now[0] = refreshed + timedelta(hours=24)
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


async def test_list_endpoint_not_found_uses_stale_cache_but_detail_not_found_is_specific():
    now = datetime.fromisoformat("2026-10-18T00:00:00Z")
    state = {"list_missing": False}
    settings = Settings.from_dict({"api_key": "key"})

    def upstream(request):
        if request.url.path.endswith("event/") and not state["list_missing"]:
            return httpx.Response(
                200, json={"current": 1, "pageSize": 100, "total": 1, "data": [row("x")]}
            )
        return httpx.Response(404)

    app = Application(
        settings, FurryConsSource(settings, httpx.MockTransport(upstream)), Renderer(),
        clock=lambda: now,
    )
    try:
        assert (await app.execute("查询")).image
        state["list_missing"] = True
        result = await app.execute("查询")
        assert result.image and "使用旧缓存" in app.renderer.lists[-1]["range"]
        assert "未找到该展会" in (await app.execute("详情 x")).text
        app.settings = replace(settings, stale_hours=0)
        assert "数据源暂不可用" in (await app.execute("查询")).text
    finally:
        await app.close()


async def test_catalog_write_failure_does_not_hide_fresh_results(monkeypatch):
    now = datetime.fromisoformat("2026-10-18T00:00:00Z")
    settings = Settings.from_dict({"api_key": "key"})

    def upstream(_request):
        return httpx.Response(
            200, json={"current": 1, "pageSize": 100, "total": 1, "data": [row("x")]}
        )

    cache = CatalogCache()

    def fail_write(*_args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(cache, "_write", fail_write)
    app = Application(
        settings, FurryConsSource(settings, httpx.MockTransport(upstream)), Renderer(),
        clock=lambda: now, cache=cache,
    )
    try:
        result = await app.execute("查询")
        assert result.image and app.renderer.lists[-1]["events"][0]["id"] == "x"
        assert "使用旧缓存" not in app.renderer.lists[-1]["range"]
    finally:
        await app.close()


@pytest.mark.parametrize("upstream_unavailable", [False, True])
async def test_locked_optional_cache_does_not_block_fresh_query_or_hide_source_failure(
    tmp_path, upstream_unavailable,
):
    calls = []

    def upstream(request):
        calls.append(request.url.path)
        if upstream_unavailable:
            return httpx.Response(503)
        return httpx.Response(
            200, json={"current": 1, "pageSize": 100, "total": 1, "data": [row("fresh")]},
        )

    settings = Settings.from_dict({"api_key": "offline-test"})
    database = tmp_path / "cache.sqlite3"
    cache = CatalogCache(database)
    blocker = sqlite3.connect(database)
    blocker.execute("BEGIN EXCLUSIVE")
    app = Application(
        settings, FurryConsSource(settings, httpx.MockTransport(upstream)), Renderer(),
        clock=lambda: datetime.fromisoformat("2026-10-18T00:00:00Z"), cache=cache,
    )
    try:
        started = time.monotonic()
        reply = await app.execute("查询")
        assert time.monotonic() - started < 1
        assert len(calls) == 1
        if upstream_unavailable:
            assert reply.image is None and "数据源暂不可用" in reply.text
        else:
            assert reply.image and app.renderer.lists[-1]["events"][0]["id"] == "fresh"
            assert "使用旧缓存" not in app.renderer.lists[-1]["range"]
    finally:
        blocker.rollback()
        blocker.close()
        await app.close()
