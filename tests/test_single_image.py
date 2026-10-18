from test_query import app_for, row

from furrycons.errors import RenderError


async def test_single_image_includes_all_results_not_only_current_page():
    app = app_for([row(f"{i:03d}", name="很长的展会名称" * 8 + str(i)) for i in range(37)])
    try:
        result = await app.execute("查询 --single")
        view = app.renderer.lists[-1]
        assert result.image and view["pages"] == 1
        assert len(view["events"]) == 37
        assert view["events"][0]["number"] == 1 and view["events"][-1]["number"] == 37
        assert view["events"][0]["id"] == "000" and view["events"][-1]["id"] == "036"
        assert "全部结果单图" in view["range"]
        await app.execute("查询")
        code = app.renderer.lists[-1]["code"]
        assert len(app.renderer.lists[-1]["events"]) == 8
        await app.execute(f"单图 {code}")
        assert len(app.renderer.lists[-1]["events"]) == 37
    finally:
        await app.close()


async def test_single_image_renderer_failure_is_reported_without_paginated_fallback():
    app = app_for([row(i) for i in range(30)])

    async def fail(_):
        raise RenderError("资源不足，单图生成失败")

    app.renderer.listing = fail
    try:
        reply = await app.execute("查询 --single")
        assert reply.image is None and "单图生成失败" in reply.text
        assert not app.renderer.lists
    finally:
        await app.close()
