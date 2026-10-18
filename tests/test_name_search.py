import httpx
from test_query import app_for, row


async def test_name_search_multiple_pages_and_single_match_are_always_candidates():
    rows = [
        row(i, "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z", name=f"FurCon 共同名称 {i}")
        for i in range(10)
    ]
    rows.append(row("cancelled", name="FurCon 已取消", status="cancelled"))
    app = app_for(rows)
    try:
        result = await app.execute("搜索 furcon", chat="group")
        assert result.image and not app.renderer.details
        view = app.renderer.lists[-1]
        assert view["total"] == 10 and len(view["events"]) == 8
        assert "日期不限" in view["range"]
        code = view["code"]
        rows.reverse()
        await app.execute(f"翻页 {code} 2", chat="group")
        assert [e["number"] for e in app.renderer.lists[-1]["events"]] == [9, 10]
        await app.execute("搜索 共同名称 9", chat="group")
        assert app.renderer.lists[-1]["total"] == 1 and not app.renderer.details
        assert "没有符合条件" in (await app.execute("搜索 不存在")).text
        assert "请提供名称" in (await app.execute("搜索")).text
    finally:
        await app.close()


async def test_search_selects_original_candidate_after_source_rename():
    app = app_for([row("same", name="旧名称")])
    try:
        await app.execute("搜索 旧名称", chat="group")
        code = app.renderer.lists[-1]["code"]
        await app.source.client.aclose()
        app.source.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=row("same", name="最新名称", detail="新资料"))
            )
        )
        await app.execute(f"选择 {code} 1", chat="group")
        assert app.renderer.details[-1]["id"] == "same"
        assert app.renderer.details[-1]["name"] == "最新名称"
    finally:
        await app.close()
