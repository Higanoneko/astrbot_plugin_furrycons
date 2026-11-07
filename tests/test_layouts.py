import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Page, async_playwright
from test_images import URL, picture
from test_query import app_for, row
from test_themes import theme_zip

from furrycons.config import Settings
from furrycons.errors import UserError
from furrycons.images import ImageCache
from furrycons.models import Convention
from furrycons.preview import preview_data
from furrycons.query import SnapshotStore
from furrycons.render import BrowserRenderer
from furrycons.themes import ThemeStore


@pytest.mark.parametrize("theme", ["material", "liquidglass"])
async def test_grid_cross_year_dates_and_missing_location_remain_readable(
    tmp_path, monkeypatch, theme,
):
    async with async_playwright() as engine:
        if not Path(engine.chromium.executable_path).is_file():
            pytest.skip("Browser regression requires: python -m playwright install chromium")

    original, metrics = Page.screenshot, []

    async def inspect(page, *args, **kwargs):
        metrics.extend(await page.evaluate("""() => Array.from(
            document.querySelectorAll('.event-card')
        ).map(card => {
            const cardBox = card.getBoundingClientRect();
            const dates = Array.from(card.querySelectorAll('.date-label')).map(date => {
                const rect = date.getBoundingClientRect();
                return {text: date.textContent, height: rect.height,
                        lineHeight: parseFloat(getComputedStyle(date).lineHeight),
                        inside: rect.left >= cardBox.left && rect.right <= cardBox.right};
            });
            const name = card.querySelector('h2').getBoundingClientRect();
            const location = card.querySelectorAll('.field')[1].getBoundingClientRect();
            return {text: card.innerText, dates, nameInside: name.right <= cardBox.right,
                    locationInside: location.right <= cardBox.right};
        })"""))
        return await original(page, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", inspect)
    renderer = BrowserRenderer(Settings(), tmp_path / "outputs")
    await renderer.images.close()
    renderer.images = ImageCache(tmp_path / "resources", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=picture((90, 360)),
                                headers={"content-type": "image/png"}),
    ))
    query = preview_data(f"builtin:{theme}")["query"]
    name = "超长中英文名称LongConventionName" * 12
    address = "长地点LongVenueAddress" * 16
    event = Convention.from_api({
        "id": "cross-year", "name": name, "address": address,
        "startDate": "2026-12-31T10:00:00Z", "endDate": "2027-01-01T10:00:00Z",
        "thumbnail": URL,
    }).detail_view("Asia/Shanghai")
    event.update(number=1, schedule_note="")
    missing = Convention.from_api({
        "id": "no-location", "name": "无地点及海报",
        "startDate": "2026-12-31T10:00:00Z", "endDate": "2027-01-01T10:00:00Z",
    }).detail_view("Asia/Shanghai")
    missing.update(number=2, schedule_note="")
    query.update(layout="grid", events=[event, missing], total=2)
    try:
        await renderer.listing(query)
        assert len(metrics) == 2
        for card in metrics:
            assert card["nameInside"] and card["locationInside"]
            assert [d["text"] for d in card["dates"]] == [
                "2026-12-31 18:00", "2027-01-01 18:00",
            ]
            assert all(d["inside"] and d["height"] <= d["lineHeight"] + 1 for d in card["dates"])
        assert name in metrics[0]["text"] and address in metrics[0]["text"]
        assert "地区未明确" in metrics[1]["text"] and "暂无展会海报" in metrics[1]["text"]
    finally:
        await renderer.close()


async def test_layout_priority_quotes_users_and_reopen(tmp_path):
    app = app_for([row(str(i)) for i in range(6)], page_size=2)
    app.settings = replace(app.settings, list_layout="poster")
    app.snapshots.close()
    app.snapshots = SnapshotStore(tmp_path / "snapshots.db")
    try:
        first = await app.execute("查询", chat="group", user="a")
        assert first.layout == "poster"
        app.record_delivery(first, chat="group", message_id="poster-image")
        changed = await app.execute(
            f"翻页 {first.query_code} 2 --layout grid", chat="group", user="a"
        )
        assert changed.layout == "grid"
        app.record_delivery(changed, chat="group", message_id="grid-image")
        assert (await app.execute(f"下一页 {first.query_code}", chat="group", user="a")).layout == "grid"
        assert (await app.execute(f"翻页 {first.query_code} 2", chat="group", user="b")).layout == "poster"
        quoted = await app.execute("下一页", chat="group", user="a", quote_id="poster-image")
        assert quoted.layout == "poster" and quoted.page == 2
        explicit = await app.execute(
            "翻页 1 --layout compact", chat="group", user="a", quote_id="grid-image"
        )
        assert explicit.layout == "compact"
        app.snapshots.close()
        app.snapshots = SnapshotStore(tmp_path / "snapshots.db")
        assert (await app.execute(f"翻页 {first.query_code} 2", chat="group", user="a")).layout == "compact"
        all_results = await app.execute(f"单图 {first.query_code} --layout grid", chat="group", user="a")
        assert all_results.layout == "grid"
        assert [e["number"] for e in app.renderer.lists[-1]["events"]] == list(range(1, 7))
    finally:
        await app.close()


async def test_legacy_theme_remains_usable_and_explicit_unsupported_layout_reports_error(tmp_path):
    app = app_for([row("x")])
    app.themes.close()
    app.themes = ThemeStore(tmp_path)
    app.themes.import_zip(theme_zip(tmp_path, manifest_changes={"layouts": []}))
    try:
        assert (await app.execute("查询 --theme local:custom")).image
        result = await app.execute("查询 --theme local:custom --layout grid")
        assert "不支持此排版" in result.text and result.image is None
        result = await app.execute("详情 x --layout grid")
        assert "仅用于" in result.text
    finally:
        await app.close()


def test_old_snapshots_and_tables_are_migrated_without_changing_identity_or_expiry(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE replies (chat TEXT, message TEXT, code TEXT, page INTEGER, PRIMARY KEY(chat,message))")
    connection.execute("CREATE TABLE page_views (chat TEXT, user TEXT, code TEXT, page INTEGER, PRIMARY KEY(chat,user,code))")
    connection.commit()
    connection.close()
    store = SnapshotStore(path)
    now = datetime.fromisoformat("2026-10-18T00:00:00Z")
    original = Convention.from_api(row("original"))
    snapshot = store.create("g", "u", [original], "范围", 8, now, 3600)
    payload = {"events": [asdict(original)], "label": "范围", "page_size": 8}
    for field in ("thumbnail", "organization_name", "organization_logo_url"):
        payload["events"][0].pop(field)
    store.connection.execute("UPDATE snapshots SET payload=?", (json.dumps(payload),))
    store.connection.commit()
    restored = store.get(snapshot.code, "g", now)
    assert restored.layout == "compact" and restored.events[0].id == "original"
    assert restored.events[0].thumbnail == "" and restored.expires == snapshot.expires
    store.bind_message("g", "m", snapshot.code, 1, "grid")
    assert store.message_layout("g", "m") == "grid"
    assert store.referenced("g", "m", now)[0].code == snapshot.code
    store.close()


@pytest.mark.parametrize("value", [None, "unknown", {}, 7])
def test_invalid_default_layouts_are_rejected(value):
    with pytest.raises(UserError):
        Settings.from_dict({"list_layout": value})
