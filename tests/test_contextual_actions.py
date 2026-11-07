import asyncio
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timedelta

import httpx
import pytest
from test_query import Renderer, row

from furrycons.app import Application
from furrycons.config import Settings
from furrycons.errors import RenderError, UserError
from furrycons.models import Convention
from furrycons.query import SnapshotStore
from furrycons.source import FurryConsSource


def contextual_app(rows, *, path=":memory:", renderer=None, now=None):
    def upstream(request):
        if request.url.path.endswith("event/"):
            return httpx.Response(200, json={
                "data": rows, "current": 1, "pageSize": 100, "total": len(rows),
            })
        return httpx.Response(200, json=row(request.url.path.rsplit("/", 1)[1]))

    settings = Settings.from_dict({
        "api_key": "fixture", "page_size": 1, "list_layout": "grid",
        "command_entry": "兽展", "function_aliases": ["看展=选择", "后页=下一页"],
    })
    return Application(
        settings, FurryConsSource(settings, httpx.MockTransport(upstream)),
        renderer or Renderer(), snapshots=SnapshotStore(path),
        clock=lambda: now[0] if now else datetime.fromisoformat("2026-10-18T00:00:00Z"),
    )


async def test_explicit_shorthand_is_private_and_preserves_page_and_layout_after_restart(tmp_path):
    rows = [row("a"), row("b"), row("c")]
    path = tmp_path / "state.db"
    app = contextual_app(rows, path=path)
    first = await app.execute("查询", chat="g", user="alice")
    await app.dispatch("后页", chat="g", user="alice")
    assert app.renderer.lists[-1]["page"] == 2
    await app.close()
    app = contextual_app(rows, path=path)
    try:
        assert (await app.dispatch("看展 2", chat="g", user="alice")).image
        assert app.renderer.details[-1]["id"] == "b"
        await app.execute("下一页", chat="g", user="alice")
        assert app.renderer.lists[-1]["page"] == 3
        assert app.renderer.lists[-1]["layout"] == "grid"
        await app.execute("上一页", chat="g", user="alice")
        assert app.renderer.lists[-1]["page"] == 2
        await app.execute("翻页 1 --layout poster", chat="g", user="alice")
        assert app.renderer.lists[-1]["page"] == 1
        await app.execute("下一页", chat="g", user="alice")
        assert app.renderer.lists[-1]["layout"] == "poster"
        for chat, user in [("other", "alice"), ("g", "bob")]:
            denied = await app.execute("下一页", chat=chat, user=user)
            assert "没有可用的最近查询" in denied.text and not denied.image
        # Reading someone else's explicit code never adopts their query.
        assert (await app.execute(f"翻页 {first.query_code} 2", chat="g", user="bob")).image
        assert "没有可用的最近查询" in (
            await app.execute("选择 1", chat="g", user="bob")
        ).text
        assert not (await app.interact("下一页", chat="g", user="alice")).image
        assert not (await app.interact("2", chat="g", user="alice")).image
    finally:
        await app.close()


async def test_quote_and_explicit_code_win_and_unknown_quote_never_uses_recent():
    rows = [row("old"), row("second")]
    app = contextual_app(rows)
    try:
        old = await app.execute("查询", chat="g", user="alice")
        app.record_delivery(old, chat="g", message_id="old-list")
        rows[:] = [row("new"), row("new-second")]
        recent = await app.execute("查询", chat="g", user="alice")
        await app.execute("选择 1", chat="g", user="alice", quote_id="old-list")
        assert app.renderer.details[-1]["id"] == "old"
        await app.execute("下一页", chat="g", user="alice", quote_id="old-list")
        assert app.renderer.lists[-1]["events"][0]["id"] == "second"
        await app.execute(
            f"选择 {recent.query_code} 1", chat="g", user="alice", quote_id="old-list"
        )
        assert app.renderer.details[-1]["id"] == "new"
        for command in ("选择 1", "翻页 2", "下一页", "上一页"):
            failed = await app.execute(command, chat="g", user="alice", quote_id="unknown")
            assert "无法关联" in failed.text and not failed.image
        await app.execute("选择 1", chat="g", user="alice")
        assert app.renderer.details[-1]["id"] == "new"
    finally:
        await app.close()


async def test_latest_expiry_does_not_fall_back_to_older_still_live_query():
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    rows = [row("old")]
    app = contextual_app(rows, now=now)
    try:
        app.settings = replace(app.settings, interaction_hours=3)
        old = await app.execute("查询", chat="g", user="alice")
        app.settings = replace(app.settings, interaction_hours=1)
        rows[:] = [row("recent")]
        await app.execute("查询", chat="g", user="alice")
        now[0] += timedelta(hours=1)
        assert "已过期" in (await app.execute("选择 1", chat="g", user="alice")).text
        assert (await app.execute(f"选择 {old.query_code} 1", chat="g", user="alice")).image
        assert app.renderer.details[-1]["id"] == "old"
    finally:
        await app.close()


@pytest.mark.parametrize("failure", ["render", "output", "unexpected"])
async def test_failed_query_does_not_replace_successful_context_after_reopen(
    tmp_path, monkeypatch, failure,
):
    path = tmp_path / "state.db"
    rows = [row("visible")]
    app = contextual_app(rows, path=path)
    first = await app.execute("查询", chat="g", user="alice")
    rows[:] = [row("invisible")]

    async def render_failure(_):
        raise RenderError("渲染失败") if failure == "render" else RuntimeError("unexpected")

    def output_failure(*_):
        raise UserError("登记失败")

    with monkeypatch.context() as patch:
        if failure == "output":
            patch.setattr(app.outputs, "register", output_failure)
        else:
            patch.setattr(app.renderer, "listing", render_failure)
        if failure == "unexpected":
            with pytest.raises(RuntimeError):
                await app.execute("查询", chat="g", user="alice")
        else:
            assert not (await app.execute("查询", chat="g", user="alice")).image
    await app.close()
    app = contextual_app(rows, path=path)
    try:
        assert app.snapshots.latest("g", "alice", app.clock()).code == first.query_code
        await app.execute("选择 1", chat="g", user="alice")
        assert app.renderer.details[-1]["id"] == "visible"
        assert (await app.execute(f"翻页 {first.query_code} 1", chat="g", user="bob")).image
    finally:
        await app.close()


async def test_in_flight_and_cancelled_query_never_becomes_recent(monkeypatch):
    rows = [row("visible")]
    app = contextual_app(rows)
    started, finish = asyncio.Event(), asyncio.Event()
    task = None
    try:
        first = await app.execute("查询", chat="g", user="alice")
        rows[:] = [row("pending")]

        async def blocked_render(_):
            started.set()
            await finish.wait()

        monkeypatch.setattr(app.renderer, "listing", blocked_render)
        task = asyncio.create_task(app.execute("查询", chat="g", user="alice"))
        await started.wait()
        assert app.snapshots.latest("g", "alice", app.clock()).code == first.query_code
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.snapshots.latest("g", "alice", app.clock()).code == first.query_code
    finally:
        if task is not None and not task.done():
            task.cancel()
        await app.close()


async def test_failed_first_and_empty_queries_have_no_recent_context_and_pending_is_pruned():
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    rows = [row("hidden")]
    app = contextual_app(rows, now=now)

    async def fail(_):
        raise RenderError("渲染失败")

    app.renderer.listing = fail
    try:
        await app.execute("查询", chat="g", user="alice")
        assert "没有可用的最近查询" in (
            await app.execute("选择 1", chat="g", user="alice")
        ).text
        rows.clear()
        assert "没有符合条件" in (await app.execute("查询", chat="g", user="alice")).text
        assert "没有可用的最近查询" in (
            await app.execute("下一页", chat="g", user="alice")
        ).text
        assert app.snapshots.connection.execute("SELECT count(*) FROM pending_snapshots").fetchone()[0] == 1
        now[0] += timedelta(hours=48)
        app.snapshots.prune(now[0])
        assert app.snapshots.connection.execute("SELECT count(*) FROM pending_snapshots").fetchone()[0] == 0
    finally:
        await app.close()


async def test_old_database_and_equal_timestamp_ordering(tmp_path):
    path = tmp_path / "legacy.db"
    now = datetime.fromisoformat("2026-10-18T00:00:00Z")
    payload = json.dumps({
        "events": [asdict(Convention.from_api(row("legacy")))],
        "label": "legacy", "page_size": 1,
    })
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE snapshots (code TEXT PRIMARY KEY, chat TEXT NOT NULL, "
                           "user TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL, "
                           "expires REAL NOT NULL)")
        connection.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
                           ("ABCDEF", "g", "alice", payload, now.timestamp(),
                            now.timestamp() + 86400))
    app = contextual_app([row("new")], path=path)
    try:
        await app.execute("选择 1", chat="g", user="alice")
        assert app.renderer.details[-1]["id"] == "legacy"
        first = await app.execute("查询", chat="g", user="alice")
        second = await app.execute("查询", chat="g", user="alice")
        assert app.snapshots.latest("g", "alice", now).code == second.query_code
        for code in ("ABCDEF", first.query_code):
            assert (await app.execute(f"翻页 {code} 1", chat="g", user="alice")).image
        assert app.snapshots.latest("g", "alice", now).code == second.query_code
    finally:
        await app.close()


async def test_page_instructions_are_concrete_and_known_actions_have_specific_usage():
    app = contextual_app([row(i) for i in range(3)])
    try:
        first = await app.execute("查询", chat="g", user="alice")
        assert f"/兽展 下一页 {first.query_code}" in first.text
        assert f"/兽展 选择 {first.query_code} 1" in first.text
        assert "上一页：" not in first.text and "<序号>" not in first.text
        middle = await app.execute("翻页 2", chat="g", user="alice")
        assert "上一页：" in middle.text and "下一页：" in middle.text
        assert f"选择 {first.query_code} 2" in middle.text
        last = await app.execute("下一页", chat="g", user="alice")
        assert "上一页：" in last.text and "下一页：" not in last.text
        single = await app.execute(f"单图 {first.query_code}", chat="g", user="alice")
        assert "上一页：" not in single.text and "下一页：" not in single.text
        for command in ("详情", "原图", "原文件", "选择", "翻页", "单图"):
            text = (await app.execute(command)).text
            assert f"用法：/兽展 {command}" in text
        for number in ("x", "-1", "１", "1234567890"):
            for action in ("选择", "翻页"):
                assert "正整数" in (
                    await app.execute(f"{action} {number}", chat="g", user="alice")
                ).text
        help_text = (await app.execute("帮助")).text
        assert "本人最近查询可简写" in help_text and "旧列表" in help_text
        assert "/FEC" not in help_text
    finally:
        await app.close()
