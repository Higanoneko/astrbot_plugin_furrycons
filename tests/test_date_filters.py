from dataclasses import replace
from datetime import datetime

import pytest
from test_query import app_for, row


async def test_days_explicit_range_and_cancelled_switch_are_same_window():
    app = app_for(
        [
            row("today"),
            row("ongoing", "2026-10-16T00:00:00Z", "2026-10-18T01:00:00Z"),
            row("tomorrow", "2026-10-19T00:00:00Z", "2026-10-19T10:00:00Z"),
            row("cancelled", status="cancelled"),
        ],
        100,
    )
    try:
        await app.execute("查询 1天")
        first = app.renderer.lists[-1]
        assert {e["id"] for e in first["events"]} == {"today", "ongoing"}
        assert first["range"].startswith("2026-10-18 至 2026-10-18")
        await app.execute("查询 2026-10-18 2026-10-18 显示取消")
        view = app.renderer.lists[-1]
        assert {e["id"] for e in view["events"]} == {"today", "ongoing", "cancelled"}
        assert "显示取消" in view["range"]
    finally:
        await app.close()


async def test_timezone_midnight_dst_and_old_snapshot_keeps_display_timezone():
    app = app_for(
        [
            row("before", "2026-11-01T03:59:59Z", "2026-11-01T03:59:59Z"),
            row("first-hour", "2026-11-01T05:30:00Z", "2026-11-01T05:31:00Z"),
            row("repeated-hour", "2026-11-01T06:30:00Z", "2026-11-01T06:31:00Z"),
            row("last", "2026-11-02T04:59:59Z", "2026-11-02T04:59:59Z"),
            row("after", "2026-11-02T05:00:00Z", "2026-11-02T05:01:00Z"),
        ],
        100,
    )
    app.settings = replace(app.settings, timezone="America/New_York")
    app.clock = lambda: datetime.fromisoformat("2026-11-01T04:30:00Z")
    try:
        await app.execute("查询 1天")
        view = app.renderer.lists[-1]
        assert {e["id"] for e in view["events"]} == {"first-hour", "repeated-hour", "last"}
        code = view["code"]
        app.settings = replace(app.settings, timezone="Asia/Shanghai")
        await app.execute(f"翻页 {code} 1")
        assert all(e["timezone"] == "America/New_York" for e in app.renderer.lists[-1]["events"])
    finally:
        await app.close()


@pytest.mark.parametrize(
    "condition",
    [
        "0天",
        "366天",
        "1天 2天",
        "2026-02-30 2026-03-01",
        "2026-10-19 2026-10-18",
        "2026-10-18",
        "2026-10-18 2026-10-19 1天",
        "wat",
    ],
)
async def test_invalid_dates_are_actionable_errors(condition):
    app = app_for([row("one")])
    try:
        result = await app.execute("查询 " + condition)
        assert result.text and not result.image
        assert "查询短码" not in result.text
    finally:
        await app.close()
