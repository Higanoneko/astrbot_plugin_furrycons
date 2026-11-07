"""Optical changes target the background and small capsules, not card content."""

import io

import httpx
import pytest
from PIL import Image
from playwright.async_api import Page
from test_glass_atmosphere import POSTER, poster_bytes, require_chromium

from furrycons.config import Settings
from furrycons.images import ImageCache
from furrycons.models import Convention
from furrycons.preview import preview_data
from furrycons.render import BrowserRenderer
from furrycons.themes import ThemeStore


def changes(first, second, region, threshold=6):
    with first.crop(region).convert("RGB") as a, second.crop(region).convert("RGB") as b:
        return sum(max(abs(x - y) for x, y in zip(p, q, strict=True)) > threshold
                   for p, q in zip(a.get_flattened_data(), b.get_flattened_data(), strict=True))


async def renderer_for(tmp_path, mode):
    themes = ThemeStore(tmp_path / "state")
    themes.set_colors("builtin:liquidglass", {"mode": mode})
    renderer = BrowserRenderer(Settings(render_timeout=120), tmp_path / "output", themes)
    await renderer.images.close()
    renderer.images = ImageCache(tmp_path / "cache", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=poster_bytes(), headers={"content-type": "image/png"}),
    ))
    return renderer, themes


@pytest.mark.parametrize("mode", ["light", "dark"])
async def test_background_optics_leave_card_poster_clear_and_borders_thin(tmp_path, monkeypatch, mode):
    await require_chromium()
    renderer, themes = await renderer_for(tmp_path, mode)
    event = preview_data("builtin:liquidglass")["event"]
    event["poster"] = {"url": POSTER, "width": 0, "height": 0}
    original, observed = Page.screenshot, {}

    async def inspect(page, *args, **kwargs):
        box = await page.locator(".event-summary").bounding_box()
        observed["clear"] = (int(box["x"] + box["width"] - 100), int(box["y"] + 35),
                             int(box["x"] + box["width"] - 20), int(box["y"] + box["height"] - 35))
        observed["borders"] = await page.locator(".info-panel,.source-panel").evaluate_all(
            "elements => elements.map(e => parseFloat(getComputedStyle(e).borderTopWidth))"
        )
        assert await page.locator(".glass-lens,.glass-optics").count() == 0
        observed["text_transform"] = await page.locator(".intro").evaluate(
            "e => [getComputedStyle(e).filter, getComputedStyle(e).transform]"
        )
        result = await original(page, *args, **kwargs)
        await page.evaluate("document.querySelector('.atmosphere-refraction img').style.transform='none'")
        observed["no_projection"] = await original(page, type="png", full_page=True)
        await page.evaluate("document.querySelector('.atmosphere-flow').style.visibility='hidden'")
        observed["no_light"] = await original(page, type="png", full_page=True)
        # Hide foreground content without changing layout so the masked optical
        # layers can be compared where they are visible, beneath the panels.
        await page.evaluate("""() => {
            document.querySelectorAll('body > :not(.detail-atmosphere)').forEach(e => {
                e.style.visibility='hidden';
            });
            document.querySelector('.atmosphere-refraction img').style.removeProperty('transform');
            document.querySelector('.atmosphere-flow').style.removeProperty('visibility');
        }""")
        observed["background"] = await original(page, type="png", full_page=True)
        await page.evaluate("document.querySelector('.atmosphere-refraction img').style.transform='none'")
        observed["background_flat"] = await original(page, type="png", full_page=True)
        await page.evaluate("document.querySelector('.atmosphere-flow').style.visibility='hidden'")
        observed["background_unlit"] = await original(page, type="png", full_page=True)
        return result

    monkeypatch.setattr(Page, "screenshot", inspect)
    try:
        path = await renderer.detail(event)
        with (Image.open(path) as painted,
              Image.open(io.BytesIO(observed["no_projection"])) as flat,
              Image.open(io.BytesIO(observed["no_light"])) as unlit):
            assert changes(painted, flat, observed["clear"]) == 0
            assert changes(flat, unlit, observed["clear"]) == 0
        with (Image.open(io.BytesIO(observed["background"])) as background,
              Image.open(io.BytesIO(observed["background_flat"])) as flat,
              Image.open(io.BytesIO(observed["background_unlit"])) as unlit):
            region = (100, int(background.height * .2), background.width - 100, int(background.height * .85))
            assert changes(background, flat, region) > 100
            assert changes(flat, unlit, region) > 100
        assert observed["borders"] == [1, 1, 1]
        assert observed["text_transform"] == ["none", "none"]
    finally:
        await renderer.close()
        themes.close()


@pytest.mark.parametrize("mode", ["light", "dark"])
async def test_nested_capsules_keep_light_tints_and_refract_existing_background(tmp_path, monkeypatch, mode):
    await require_chromium()
    renderer, themes = await renderer_for(tmp_path, mode)
    query = preview_data("builtin:liquidglass")["query"]
    query["events"] = []
    for index, status in enumerate(("scheduled", "postponed", "cancelled", "unknown"), 1):
        event = Convention.from_api({"id": f"capsule-{index}", "name": f"状态示例 {index}",
                                     "status": status, "thumbnail": POSTER}).detail_view("Asia/Shanghai")
        event.update(number=index, schedule_note="日期待定（不归入日程）" if status == "postponed" else "")
        query["events"].append(event)
    query.update(total=4, layout="grid")
    original, observed = Page.screenshot, {}

    async def inspect(page, *args, **kwargs):
        # A controlled stripe substrate makes actual backdrop displacement
        # distinguishable from the tint and blur, even with a mostly plain poster.
        await page.evaluate("""() => document.querySelectorAll('.card-info').forEach(e => {
            e.style.background='repeating-linear-gradient(90deg,#234977 0 18px,#fac36c 18px 36px)';
        })""")
        observed["boxes"] = await page.locator(".event-card .tag").evaluate_all(
            "elements => elements.map(e => { const r=e.getBoundingClientRect(); return [r.x,r.y,r.right,r.bottom]; })"
        )
        observed["styles"] = await page.locator(".event-card .tag").evaluate_all(
            "elements => elements.map(e => { const s=getComputedStyle(e); return {text:e.innerText,color:s.color,border:s.borderStyle,width:s.borderTopWidth,filter:s.filter,transform:s.transform,backdrop:s.backdropFilter,background:s.backgroundColor,image:s.backgroundImage,opacity:s.opacity,veil:getComputedStyle(e,'::before').display}; })"
        )
        observed["query_code"] = await page.locator(".query-code").evaluate(
            "e => { const s=getComputedStyle(e); return {background:s.backgroundColor,image:s.backgroundImage,opacity:s.opacity,backdrop:s.backdropFilter,veil:getComputedStyle(e,'::before').display}; }"
        )
        assert await page.locator(".field .note").inner_text() == "日期待定（不归入日程）"
        observed["note"] = await page.locator(".field .note").evaluate(
            "e => { const s=getComputedStyle(e); return [s.backgroundColor,s.backgroundImage,s.borderTopWidth,s.boxShadow,s.backdropFilter,s.paddingTop,s.paddingLeft]; }"
        )
        result = await original(page, *args, **kwargs)
        await page.evaluate("""() => document.querySelectorAll('.tag').forEach(e => {
            e.style.backdropFilter='none';
        })""")
        observed["no_displacement"] = await original(page, type="png", full_page=True)
        return result

    monkeypatch.setattr(Page, "screenshot", inspect)
    try:
        path = await renderer.listing(query)
        with Image.open(path) as painted, Image.open(io.BytesIO(observed["no_displacement"])) as flat:
            assert sum(changes(painted, flat, tuple(map(int, box))) for box in observed["boxes"]) > 20
        styles = observed["styles"]
        assert [s["text"] for s in styles] == ["计划举办", "延期 · 日期待定", "已取消", "未知状态（unknown）"]
        assert styles[0]["color"] != styles[1]["color"] and styles[2]["border"] == "dashed"
        assert observed["note"] == ["rgba(0, 0, 0, 0)", "none", "0px", "none", "none", "0px", "0px"]
        assert all(s["width"] == "1px" and s["filter"] == "none" and s["transform"] == "none"
                   and "fc-capsule-refraction" in s["backdrop"] for s in styles)
        assert styles[0]["background"] != styles[1]["background"]
        assert all(s["background"] != "rgba(0, 0, 0, 0)" and s["image"] == "none"
                   and s["opacity"] == "1" and s["veil"] == "none"
                   and "blur(" not in s["backdrop"] and "saturate(" not in s["backdrop"]
                   for s in [*styles, observed["query_code"]])
    finally:
        await renderer.close()
        themes.close()
