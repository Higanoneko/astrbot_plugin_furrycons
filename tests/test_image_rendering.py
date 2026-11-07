import struct
from itertools import pairwise
from pathlib import Path

import httpx
import pytest
from PIL import Image
from playwright.async_api import Page, async_playwright
from test_images import URL, picture

from furrycons.config import Settings
from furrycons.images import ImageCache
from furrycons.models import Convention
from furrycons.preview import preview_data
from furrycons.render import BrowserRenderer
from furrycons.themes import ThemeStore


async def require_chromium():
    async with async_playwright() as engine:
        if not Path(engine.chromium.executable_path).is_file():
            pytest.skip("Browser regression requires: python -m playwright install chromium")


@pytest.mark.parametrize("theme", ["material", "liquidglass"])
async def test_grid_dates_stay_whole_with_poster_and_long_text(tmp_path, monkeypatch, theme):
    await require_chromium()
    original, metrics = Page.screenshot, {}

    async def inspect(page, *args, **kwargs):
        metrics.update(await page.evaluate("""() => ({
            width: document.documentElement.scrollWidth,
            dates: Array.from(document.querySelectorAll('.date-label')).map(e => ({
                height: e.getBoundingClientRect().height,
                lineHeight: parseFloat(getComputedStyle(e).lineHeight),
                inside: e.getBoundingClientRect().right <=
                    e.closest('.event-card').getBoundingClientRect().right,
            })),
            text: document.querySelector('.event-card').innerText
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
    event = {**query["events"][0], "poster": {"url": URL, "width": 0, "height": 0},
             "name": "超长展会名称" * 20, "address": "超长地址" * 30}
    query.update(layout="grid", events=[event], total=1)
    try:
        await renderer.listing(query)
        assert metrics["width"] == 1080
        assert len(metrics["dates"]) == 2
        assert all(date["inside"] and date["height"] <= date["lineHeight"] + 1
                   for date in metrics["dates"])
        assert event["name"] in metrics["text"] and event["address"] in metrics["text"]
    finally:
        await renderer.close()


@pytest.mark.parametrize("theme", ["material", "liquidglass"])
@pytest.mark.parametrize("layout", ["compact", "poster", "grid"])
async def test_real_browser_fills_card_height_and_keeps_text_when_one_image_fails(
    tmp_path, monkeypatch, theme, layout,
):
    await require_chromium()
    requests, metrics = [], []

    def upstream(request):
        requests.append(request)
        assert "authorization" not in request.headers
        if request.url.path.endswith("missing.png"):
            return httpx.Response(404)
        return httpx.Response(200, content=picture((90, 360)), headers={"content-type": "image/png"})

    original = Page.screenshot

    async def inspect(page, *args, **kwargs):
        metrics.append(await page.evaluate("""() => ({
            width: document.documentElement.scrollWidth,
            cards: Array.from(document.querySelectorAll('.event-card')).map(e => e.innerText),
            images: Array.from(document.querySelectorAll('.poster-image')).map(i => ({
                fit: getComputedStyle(i).objectFit, width: i.naturalWidth, height: i.naturalHeight,
                naturalRatio: Math.abs(i.getBoundingClientRect().width
                    / i.getBoundingClientRect().height - i.naturalWidth / i.naturalHeight) < .001,
                fullHeight: Math.abs(i.getBoundingClientRect().height
                    - i.parentElement.getBoundingClientRect().height) < .1
            })),
            geometry: Array.from(document.querySelectorAll('.event-card,.event-summary')).map(card => {
                const text = card.querySelector('.card-info,.summary-copy').getBoundingClientRect();
                const image = card.querySelector('.poster-frame').getBoundingClientRect();
                const container = card.getBoundingClientRect();
                return {
                    hasPoster: !!card.querySelector('.poster-image'),
                    imageBehindInformation: !card.querySelector('.poster-image')
                        || image.left <= text.left,
                    imageFillsHeight: Math.abs(image.top - container.top) <= 1.1
                        && Math.abs(image.bottom - container.bottom) <= 1.1,
                    placeholderBelowInformation: image.top >= text.bottom,
                    imageTouchesRight: Math.abs(image.right - container.right) <= 1.1,
                    imageInsideCard: image.left >= container.left && image.right <= container.right
                        && image.top >= container.top && image.bottom <= container.bottom
                };
            }),
            text: document.body.innerText
        })"""))
        return await original(page, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", inspect)
    renderer = BrowserRenderer(Settings(), tmp_path / "outputs")
    await renderer.images.close()
    renderer.images = ImageCache(tmp_path / "resources", transport=httpx.MockTransport(upstream))
    view = preview_data(f"builtin:{theme}")
    portrait = Convention.from_api({
        "id": "portrait", "name": "竖长海报 · " + "长名称完整显示" * 6,
        "thumbnail": URL, "status": "scheduled", "organization": {"name": "主办方", "logoUrl": URL},
    }).detail_view("Asia/Shanghai")
    portrait.update(number=1, schedule_note="")
    view["query"].update(layout=layout, total=3)
    view["query"]["events"] = [portrait, {
        **portrait, "id": "failed-image", "number": 2, "name": "海报故障仍完整显示",
        "poster": {"url": "https://images.furrycons.cn/missing.png", "width": 0, "height": 0},
    }, {**portrait, "id": "last", "number": 3, "name": "最后一场仍完整显示"}]
    try:
        path = await renderer.listing(view["query"])
        assert struct.unpack(">II", path.read_bytes()[16:24])[0] == 1080
        result = metrics[-1]
        assert result["width"] == 1080 and len(result["cards"]) == 3
        assert "海报暂不可用" in result["cards"][1]
        assert "最后一场仍完整显示" in result["cards"][2]
        assert len(result["geometry"]) == 3
        assert all(item["imageBehindInformation"] and
                   (item["imageFillsHeight"] if item["hasPoster"] or layout != "grid"
                    else item["placeholderBelowInformation"])
                   and (item["imageTouchesRight"] if item["hasPoster"] or layout != "grid" else True)
                   and item["imageInsideCard"]
                   for item in result["geometry"])
        assert all(item == {"fit": "contain", "width": 90, "height": 360,
                            "naturalRatio": True, "fullHeight": True}
                   for item in result["images"])
        assert len(requests) == 2  # Poster and organizer logo reuse the same resource.
        portrait["theme_key"] = f"builtin:{theme}"
        assert (await renderer.detail(portrait)).is_file()
        assert "主办方" in metrics[-1]["text"]
        assert metrics[-1]["geometry"] == [{
            "hasPoster": True,
            "imageBehindInformation": True, "imageFillsHeight": True,
            "placeholderBelowInformation": False,
            "imageTouchesRight": True, "imageInsideCard": True,
        }]
        assert len(requests) == 2
    finally:
        await renderer.close()


@pytest.mark.parametrize("theme", ["material", "liquidglass"])
@pytest.mark.parametrize("layout", ["compact", "poster", "grid"])
async def test_poster_blends_continuously_under_information(tmp_path, monkeypatch, theme, layout):
    await require_chromium()
    original, samples = Page.screenshot, {}

    async def inspect(page, *args, **kwargs):
        # Isolate the actual poster composition on black to measure its opacity.
        # The production image, mask, blur and card geometry stay in use.
        # add_style_tag waits for page callbacks, unavailable with page scripts disabled.
        await page.evaluate("""() => {
            const style = document.createElement('style');
            style.textContent = `
                .event-card {background:#000!important;border-color:transparent!important;
                             box-shadow:none!important}
                .event-card::before,.event-card::after,.ambient-wash,.card-info,.glass-optics,.glass-lens {visibility:hidden}
            `;
            document.head.append(style);
        }""")
        frame = await page.locator(".poster-frame").bounding_box()
        image = await page.locator(".poster-image").bounding_box()
        # The portrait is narrow; the whole left part must come from sampled fill.
        assert image["x"] > frame["x"] + frame["width"] / 2
        samples.update(
            edge=(round(frame["x"] + frame["width"] * .15),
                  round(frame["y"] + frame["height"] / 2)),
            clear=(round(frame["x"] + frame["width"] * .98),
                   round(frame["y"] + frame["height"] / 2)),
            row=(round(frame["x"] + frame["width"] * .04),
                 round(frame["y"] + frame["height"] / 2),
                 round(frame["x"] + frame["width"] * .96)),
        )
        return await original(page, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", inspect)
    renderer = BrowserRenderer(Settings(), tmp_path / "outputs")
    await renderer.images.close()
    renderer.images = ImageCache(tmp_path / "resources", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=picture((90, 360), "#00ff00"),
                                headers={"content-type": "image/png"}),
    ))
    query = preview_data(f"builtin:{theme}")["query"]
    event = {**query["events"][0], "poster": {"url": URL, "width": 0, "height": 0}}
    query.update(events=[event], total=1, layout=layout)
    try:
        path = await renderer.listing(query)
        with Image.open(path) as screenshot:
            edge = screenshot.convert("RGB").getpixel(samples["edge"])
            clear = screenshot.convert("RGB").getpixel(samples["clear"])
            assert edge[0] <= 1 and 24 <= edge[1] <= 28 and edge[2] <= 1
            assert clear == (0, 255, 0)
            start, y, end = samples["row"]
            rgb = screenshot.convert("RGB")
            profile = [rgb.getpixel((x, y))[1] for x in range(start, end)]
            assert min(profile) >= 24  # The image reaches beneath the text.
            # The old clipped image jumped from 0 to 26 at an interior boundary.
            assert max(abs(a - b) for a, b in pairwise(profile)) <= 3
    finally:
        await renderer.close()


@pytest.mark.parametrize("theme", ["material", "liquidglass"])
async def test_height_drives_portrait_landscape_and_unrestricted_wide_posters(
    tmp_path, monkeypatch, theme,
):
    await require_chromium()
    sizes = [(90, 360), (640, 360), (1600, 180)]
    original, metrics = Page.screenshot, []

    async def inspect(page, *args, **kwargs):
        metrics.extend(await page.evaluate("""() => Array.from(
            document.querySelectorAll('.poster-image')
        ).map(image => {
            const box = image.getBoundingClientRect();
            const frame = image.parentElement.getBoundingClientRect();
            return {width:box.width,height:box.height,frameWidth:frame.width,
                    frameHeight:frame.height,rightAligned:Math.abs(box.right-frame.right)<.1};
        })"""))
        return await original(page, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", inspect)
    renderer = BrowserRenderer(Settings(), tmp_path / "outputs")
    await renderer.images.close()
    renderer.images = ImageCache(tmp_path / "resources", transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=picture(sizes[int(request.url.params["size"])], "#225588"),
            headers={"content-type": "image/png"},
        ),
    ))
    query = preview_data(f"builtin:{theme}")["query"]
    seed = query["events"][0]
    query.update(total=3, events=[{
        **seed, "number":i+1, "id":f"ratio-{i}",
        "poster":{"url":f"{URL}?size={i}", "width":0, "height":0},
    } for i in range(3)])
    try:
        await renderer.listing(query)
        for item, (width, height) in zip(metrics, sizes, strict=True):
            assert item["height"] == pytest.approx(item["frameHeight"], abs=.1)
            assert item["width"] == pytest.approx(item["height"] * width / height, abs=.1)
            assert item["rightAligned"]
        assert metrics[0]["width"] < metrics[0]["frameWidth"] / 2
        assert metrics[-1]["width"] > metrics[-1]["frameWidth"] * 2
    finally:
        await renderer.close()


async def test_long_glass_screenshot_actually_paints_footer_text(tmp_path, monkeypatch):
    await require_chromium()
    themes = ThemeStore(tmp_path / "state")
    themes.set_colors("builtin:liquidglass", {"mode": "dark"})
    renderer = BrowserRenderer(Settings(render_timeout=120), tmp_path / "outputs", themes)
    await renderer.images.close()
    renderer.images = ImageCache(tmp_path / "resources", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=picture((90, 360), "#225588"),
                                headers={"content-type": "image/png"}),
    ))
    query = preview_data("builtin:liquidglass")["query"]
    seed = {**query["events"][0], "poster": {"url": URL, "width": 0, "height": 0}}
    query.update(total=60, events=[
        {**seed, "number": i, "id": f"long-{i}", "name": f"第 {i} 场长图检查"}
        for i in range(1, 61)
    ])
    original, footer = Page.screenshot, {}

    async def inspect(page, *args, **kwargs):
        footer.update(await page.locator("footer").bounding_box())
        return await original(page, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", inspect)
    try:
        path = await renderer.listing(query)
        with Image.open(path) as image:
            assert image.height > 16_384
            region = image.crop((40, int(footer["y"]), 1000,
                                 int(footer["y"] + footer["height"]))).convert("RGB")
            # This caught real Chromium output with a complete DOM and PNG
            # dimensions, but zero painted footer glyphs under backdrop filters.
            ink = sum(r > 150 and g > 150 and b > 150 for r, g, b in region.get_flattened_data())
            assert ink > 200
    finally:
        await renderer.close()
        themes.close()
