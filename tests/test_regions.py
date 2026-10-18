from test_query import app_for, row


async def test_province_full_short_and_abbreviation_include_known_cities_all_pages():
    app = app_for(
        [row(i, region="深圳市") for i in range(9)]
        + [
            row("gz", region="广州"),
            row("sz", region="苏州"),
            row("unknown", region="未来城", address="广东省深圳市"),
            row("missing", region=""),
        ],
        100,
    )
    try:
        for region in ("广东", "广东省", "粤"):
            await app.execute("查询 " + region)
            events = app.renderer.lists[-1]["events"]
            assert len(events) == 10 and "gz" in {e["id"] for e in events}
            assert "unknown" not in {e["id"] for e in events}
        await app.execute("查询 深圳")
        assert app.renderer.lists[-1]["total"] == 9
        await app.execute("查询 广东省 深圳市")
        assert app.renderer.lists[-1]["total"] == 9
        await app.execute("查询")
        unknown = next(e for e in app.renderer.lists[-1]["events"] if e["id"] == "unknown")
        assert unknown["region"] == "未来城（省市归属未明确）"
        missing = next(e for e in app.renderer.lists[-1]["events"] if e["id"] == "missing")
        assert missing["region"] == "地区未明确"
    finally:
        await app.close()


async def test_ambiguous_places_are_not_guessed_but_qualified_names_resolve():
    app = app_for(
        [
            row("liaoning", region="朝阳市"),
            row("bj", region="北京市朝阳区"),
            row("ambiguous", region="朝阳"),
        ],
        100,
    )
    try:
        assert "有歧义" in (await app.execute("查询 朝阳")).text
        await app.execute("查询 辽宁省朝阳市")
        assert [e["id"] for e in app.renderer.lists[-1]["events"]] == ["liaoning"]
        assert "未识别地区" in (await app.execute("查询 不存在地区")).text
    finally:
        await app.close()


async def test_county_city_hierarchy_and_traditional_place_aliases():
    app = app_for(
        [
            row("yiwu", region="义乌"),
            row("taipei", region="臺北市"),
            row("taoyuan", region="桃園市"),
            row("hk", region="香港特别行政区"),
        ],
        100,
    )
    try:
        for query, expected in [
            ("浙江", {"yiwu"}),
            ("金华", {"yiwu"}),
            ("台湾", {"taipei", "taoyuan"}),
            ("台北", {"taipei"}),
            ("港", {"hk"}),
        ]:
            await app.execute("查询 " + query)
            assert {e["id"] for e in app.renderer.lists[-1]["events"]} == expected
    finally:
        await app.close()
