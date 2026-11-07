"""The detail atmosphere reuses its own poster and remains safe for long renders."""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw
from playwright.async_api import Page, async_playwright

from furrycons.config import Settings
from furrycons.images import ImageCache
from furrycons.preview import preview_data
from furrycons.render import BrowserRenderer
from furrycons.themes import ThemeStore

POSTER = "https://images.furrycons.cn/glass-atmosphere-test.png"


async def require_chromium():
    async with async_playwright() as engine:
        if not Path(engine.chromium.executable_path).is_file():
            pytest.skip("Browser regression requires: python -m playwright install chromium")


def poster_bytes(size=(480, 720)):
    with Image.new("RGB", size, "#f2943e") as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, size[0] // 2, size[1] // 2), fill="#244cbd")
        draw.rectangle((size[0] // 2, size[1] // 2, size[0], size[1]), fill="#37a886")
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("availability", ["present", "missing", "failed"])
async def test_detail_uses_own_poster_once_and_keeps_fallback_readable(
    tmp_path, monkeypatch, mode, availability,
):
    await require_chromium()
    themes = ThemeStore(tmp_path / "state")
    themes.set_colors("builtin:liquidglass", {"mode": mode})
    renderer = BrowserRenderer(Settings(), tmp_path / "outputs", themes)
    await renderer.images.close()
    requests = []

    def upstream(request):
        requests.append(request)
        return (httpx.Response(404) if availability == "failed" else
                httpx.Response(200, content=poster_bytes(), headers={"content-type": "image/png"}))

    renderer.images = ImageCache(tmp_path / "cache", transport=httpx.MockTransport(upstream))
    event = preview_data("builtin:liquidglass")["event"]
    if availability != "missing":
        event["poster"] = {"url": POSTER, "width": 0, "height": 0}
    original, observed = Page.screenshot, {}

    async def inspect(page, *args, **kwargs):
        observed.update(await page.evaluate("""() => {
            const atmosphere = document.querySelector('.detail-atmosphere img');
            const poster = document.querySelector('.poster-image');
            return {
                background: atmosphere?.src || null, poster: poster?.src || null,
                intro: document.querySelector('.intro').innerText,
                textFilter: getComputedStyle(document.querySelector('.intro')).filter,
                source: document.querySelector('.source-panel').innerText,
                width: document.documentElement.scrollWidth,
                backgroundHidden: atmosphere?.parentElement.getAttribute('aria-hidden'),
            };
        }"""))
        return await original(page, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", inspect)
    try:
        path = await renderer.detail(event)
        assert observed["width"] == 1080 and observed["intro"] == event["introduction"]
        assert event["source"] in observed["source"] and observed["textFilter"] == "none"
        assert len(requests) == (0 if availability == "missing" else 1)
        assert all("authorization" not in request.headers for request in requests)
        if availability == "present":
            assert observed["background"] == observed["poster"]
            assert observed["backgroundHidden"] == "true"
            with Image.open(path) as screenshot:
                # Sample only the exposed page margin, outside text and cards.
                samples = [screenshot.getpixel((12, int(screenshot.height * ratio)))[:3]
                           for ratio in (.3, .5, .7)]
                assert max(max(values) - min(values) for values in zip(*samples, strict=True)) > 25
        else:
            assert observed["background"] is None and observed["poster"] is None
    finally:
        await renderer.close()
        themes.close()


@pytest.mark.parametrize("mode", ["light", "dark"])
async def test_long_detail_paints_last_paragraph_and_footer(tmp_path, monkeypatch, mode):
    await require_chromium()
    themes = ThemeStore(tmp_path / "state")
    themes.set_colors("builtin:liquidglass", {"mode": mode})
    renderer = BrowserRenderer(Settings(render_timeout=120), tmp_path / "outputs", themes)
    await renderer.images.close()
    renderer.images = ImageCache(tmp_path / "cache", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=poster_bytes((1200, 240)),
                                headers={"content-type": "image/png"}),
    ))
    event = preview_data("builtin:liquidglass")["event"]
    event.update(poster={"url": POSTER, "width": 0, "height": 0},
                 introduction="\n\n".join("山海相逢，玻璃映出展会色彩。" * 8 for _ in range(70)))
    original, observed = Page.screenshot, {}

    async def inspect(page, *args, **kwargs):
        observed["footer"] = await page.locator("footer").bounding_box()
        observed["text"] = await page.locator(".intro").bounding_box()
        observed["long"] = await page.locator("html").get_attribute("class")
        # Compare actual text painting against the same background with just
        # foreground ink hidden; a correct DOM alone missed a past compositor bug.
        result = await original(page, *args, **kwargs)
        await page.evaluate("""() => {
            document.querySelector('footer').style.visibility = 'hidden';
            document.querySelector('.intro').style.visibility = 'hidden';
        }""")
        observed["without_text"] = await original(page, type="png", full_page=True)
        return result

    monkeypatch.setattr(Page, "screenshot", inspect)
    try:
        path = await renderer.detail(event)
        with Image.open(path) as image, Image.open(io.BytesIO(observed["without_text"])) as empty:
            assert image.height > 8192 and "long-image" in observed["long"]
            for box, last in ((observed["footer"], False), (observed["text"], True)):
                bottom = int(box["y"] + box["height"])
                region = (int(box["x"]), max(int(box["y"]), bottom - 80) if last else int(box["y"]),
                          int(box["x"] + box["width"]), bottom)
                ink = image.crop(region).convert("RGB")
                blank = empty.crop(region).convert("RGB")
                changed = sum(max(abs(a - b) for a, b in zip(p, q, strict=True)) > 35
                              for p, q in zip(ink.get_flattened_data(), blank.get_flattened_data(), strict=True))
                assert changed > 200
    finally:
        await renderer.close()
        themes.close()
