from datetime import datetime
from pathlib import Path

import httpx

from furrycons.app import Application
from furrycons.config import Settings
from furrycons.source import FurryConsSource


class Renderer:
    def __init__(self):
        self.lists = []
        self.details = []

    async def detail(self, view):
        self.details.append(view)
        return Path("original-detail.png")

    async def listing(self, view):
        self.lists.append(view)
        return Path("original-list.png")

    async def close(self):
        pass


def row(identity, start="2026-10-18T00:00:00Z", end="2026-10-18T10:00:00Z", **changes):
    return {
        "id": str(identity),
        "name": f"展会{identity}",
        "startDate": start,
        "endDate": end,
        "status": "scheduled",
        **changes,
    }


def app_for(rows, page_size=8):
    def upstream(request):
        current = int(request.url.params["current"])
        size = 3  # Upstream may clamp the requested pageSize.
        return httpx.Response(
            200,
            json={
                "data": rows[(current - 1) * size : current * size],
                "total": len(rows),
                "current": current,
                "pageSize": size,
            },
        )

    settings = Settings.from_dict(
        {"api_key": "test-key", "page_size": page_size, "cache_minutes": 0, "stale_hours": 0}
    )
    return Application(
        settings,
        FurryConsSource(settings, httpx.MockTransport(upstream)),
        Renderer(),
        clock=lambda: datetime.fromisoformat("2026-10-17T16:30:00Z"),
    )


async def test_all_upstream_pages_first_page_continuous_numbers_and_stability():
    rows = [row(i) for i in range(12)]
    app = app_for(rows)
    try:
        first = await app.execute("查询", chat="group", user="a")
        assert first.image
        view = app.renderer.lists[-1]
        assert view["total"] == 12 and len(view["events"]) == 8
        assert view["range"].startswith("2026-10-18 至 2027-10-17 · Asia/Shanghai")
        code, original = view["code"], view["events"][0]["id"]
        rows.reverse()
        rows.insert(0, row("new"))
        await app.execute(f"翻页 {code} 2", chat="group", user="b")
        assert [e["number"] for e in app.renderer.lists[-1]["events"]] == [9, 10, 11, 12]
        await app.execute(f"翻页 {code} 1", chat="group")
        assert app.renderer.lists[-1]["events"][0]["id"] == original
        assert "没有该查询" in (await app.execute(f"翻页 {code} 1", chat="another")).text
        assert "超出范围" in (await app.execute(f"翻页 {code} 0", chat="group")).text
        await app.execute("查询", chat="group")
        assert app.renderer.lists[-1]["code"] != code
        assert app.renderer.lists[-1]["total"] == 13
    finally:
        await app.close()


async def test_overlap_inclusive_first_day_exclusive_day_366_and_undated():
    rows = [
        row("ongoing", "2026-10-16T00:00:00Z", "2026-10-18T10:00:00Z"),
        row("at-start", "2026-10-17T00:00:00Z", "2026-10-17T16:00:00Z"),
        row("last-day", "2027-10-17T15:59:59Z", "2027-10-17T15:59:59Z"),
        row("after", "2027-10-17T16:00:00Z", "2027-10-18T00:00:00Z"),
        row("past", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
        row("cancelled", status="cancelled"),
        row("tbd", "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z", status="postponed"),
        row("unknown", status="new-enum"),
        row("missing", end=""),
        row("invalid", start="bad-date"),
    ]
    app = app_for(rows, page_size=100)
    try:
        await app.execute("查询")
        events = app.renderer.lists[-1]["events"]
        assert {e["id"] for e in events} == {
            "ongoing",
            "at-start",
            "last-day",
            "tbd",
            "unknown",
            "missing",
            "invalid",
        }
        assert all(e["schedule_note"] for e in events if e["id"] in {"tbd", "missing", "invalid"})
        assert "未知状态" in next(e for e in events if e["id"] == "unknown")["status_label"]
    finally:
        await app.close()


async def test_zero_results_are_distinct_from_network_failure():
    app = app_for([])
    try:
        assert "没有符合条件" in (await app.execute("查询")).text
        app.source.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        )
        assert "暂不可用" in (await app.execute("查询")).text
    finally:
        await app.close()


async def test_duplicate_or_inconsistent_upstream_does_not_claim_complete_results():
    app = app_for([row("duplicate")] * 8)
    try:
        result = await app.execute("查询")
        assert "未提供不完整结果" in result.text and not result.image
    finally:
        await app.close()
