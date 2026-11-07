import asyncio
import io

import httpx
import pytest
from PIL import Image

from furrycons.image_colors import edge_colors
from furrycons.images import TTL_SECONDS, ImageCache, official_image_url
from furrycons.models import Convention

URL = "https://images.furrycons.cn/organizations/example/cover.jpeg"


def picture(size=(30, 60), color="blue"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


def test_edge_colors_follow_vertical_boundary_instead_of_poster_center():
    with Image.new("RGB", (90, 90), "blue") as image:
        for index, color in enumerate(("#ff0000", "#00ff00", "#ffcc00")):
            image.paste(color, (0, index * 30, 3, (index + 1) * 30))
        assert edge_colors(image) == ("#ff0000", "#00ff00", "#ffcc00")
        assert image.getpixel((0, 0)) == (255, 0, 0)


def test_edge_colors_ignore_transparent_margin_and_hidden_rgb():
    with Image.new("RGBA", (90, 90), (0, 0, 255, 0)) as image:
        image.paste((0, 255, 0, 255), (13, 0, 90, 90))
        image.paste((255, 0, 0, 255), (10, 15, 13, 75))
        assert edge_colors(image) == ("#ff0000",) * 3


def test_fully_transparent_image_has_no_invented_fill():
    with Image.new("RGBA", (90, 90), (255, 0, 0, 0)) as image:
        assert edge_colors(image) == ("transparent",) * 3


@pytest.mark.parametrize("value", [
    None, {}, "", "http://images.furrycons.cn/x.png", "https://example.org/x.png",
    "https://images.furrycons.cn.evil.test/x.png", "https://images.furrycons.cn@evil.test/x.png",
    "https://images.furrycons.cn:444/x.png", "https://images.furrycons.cn/x.png#fragment",
    "file:///etc/passwd", "data:image/png;base64,AA==", "https://images.furrycons.cn/\nx.png",
])
def test_only_official_resource_origin_is_accepted(value):
    assert official_image_url(value) is None


async def test_seven_day_reuse_survives_reopen_and_does_not_extend_expiry(tmp_path):
    now = [1_800_000_000.0]
    calls = []

    def upstream(request):
        calls.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert request.url.host == "images.furrycons.cn"
        return httpx.Response(
            200, content=picture(color="blue" if len(calls) == 1 else "red"),
            headers={"content-type": "image/png"},
        )

    cache = ImageCache(tmp_path, transport=httpx.MockTransport(upstream), clock=lambda: now[0])
    first = await cache.get(URL)
    assert (first.width, first.height) == (30, 60)
    assert first.edge_colors == ("#0000ff",) * 3
    await cache.close()
    cache = ImageCache(tmp_path, transport=httpx.MockTransport(upstream), clock=lambda: now[0])
    try:
        now[0] += TTL_SECONDS - 1
        cached = await cache.get(URL)
        assert cached.data == first.data and cached.edge_colors == first.edge_colors
        assert len(calls) == 1
        now[0] += 1
        refreshed = await cache.get(URL)
        assert refreshed.data != first.data and refreshed.edge_colors == ("#ff0000",) * 3
        assert len(calls) == 2
        assert await cache.get(URL + "?revision=2")
        assert len(calls) == 3
    finally:
        await cache.close()


@pytest.mark.parametrize("status,headers,body", [
    (302, {"location": "https://evil.test/x", "content-type": "image/png"}, b""),
    (404, {}, b""), (200, {"content-type": "text/html"}, b"<html>"),
    (200, {"content-type": "image/jpeg"}, b"not-an-image"),
    (200, {"content-type": "image/png", "content-length": "999999999"}, b""),
])
async def test_bad_optional_images_do_not_become_assets(tmp_path, status, headers, body):
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, content=body)

    cache = ImageCache(tmp_path, transport=httpx.MockTransport(upstream))
    try:
        assert await cache.get(URL) is None
        assert len(requests) == 1
        assert not list(tmp_path.glob("*.png"))
    finally:
        await cache.close()


async def test_prepare_keeps_text_and_only_maps_images_from_this_view(tmp_path):
    def upstream(request):
        if request.url.path.endswith("missing.png"):
            return httpx.Response(404)
        return httpx.Response(200, content=picture(), headers={"content-type": "image/png"})

    cache = ImageCache(tmp_path, transport=httpx.MockTransport(upstream))
    event = Convention.from_api({
        "id": "x", "name": "完整文字", "thumbnail": URL,
        "organization": {"name": "主办方", "logoUrl": "https://images.furrycons.cn/missing.png"},
    }).detail_view("Asia/Shanghai")
    try:
        prepared, resources = await cache.prepare({"event": event})
        assert prepared["event"]["name"] == "完整文字"
        assert prepared["event"]["organization_name"] == "主办方"
        assert prepared["event"]["organizer_logo"] is None
        assert prepared["event"]["poster"]["url"] in resources
        assert event["poster"]["url"] == URL  # A render cannot mutate the snapshot.
        assert len(resources) == 1
        event["poster"]["url"] = "https://images.furrycons.cn/missing.png"
        prepared, resources = await cache.prepare({"event": event})
        assert prepared["event"]["poster"] is None and not resources
        assert prepared["event"]["poster_note"] == "海报暂不可用"
    finally:
        await cache.close()


async def test_parallel_requests_share_download_and_expired_failure_never_reuses_stale(tmp_path):
    now = [1_800_000_000.0]
    calls, fail = [], [False]

    async def upstream(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(
            503 if fail[0] else 200, content=picture(), headers={"content-type": "image/png"}
        )

    cache = ImageCache(tmp_path, transport=httpx.MockTransport(upstream), clock=lambda: now[0])
    try:
        assets = await asyncio.gather(*(cache.get(URL) for _ in range(8)))
        assert all(asset.data == assets[0].data for asset in assets)
        assert len(calls) == 1
        now[0] += TTL_SECONDS
        fail[0] = True
        assert await cache.get(URL) is None
        assert len(calls) == 2
    finally:
        await cache.close()


async def test_cache_capacity_and_corruption_recover_by_requesting_official_resource(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=picture(), headers={"content-type": "image/png"})

    size = len(picture())
    cache = ImageCache(tmp_path, transport=httpx.MockTransport(upstream), max_bytes=size + 2)
    try:
        first = await cache.get(URL)
        (tmp_path / f"{first.key}.png").write_bytes(b"corrupt")
        assert await cache.get(URL)
        assert len(calls) == 2
        assert await cache.get(URL + "?different")
        assert sum(p.stat().st_size for p in tmp_path.glob("*.png")) <= size + 2
    finally:
        await cache.close()


async def test_cache_write_failure_still_supplies_image_to_current_render(tmp_path, monkeypatch):
    def upstream(request):
        return httpx.Response(200, content=picture(), headers={"content-type": "image/png"})

    cache = ImageCache(tmp_path, transport=httpx.MockTransport(upstream))
    def fail_save(asset):
        raise OSError("disk full")

    monkeypatch.setattr(cache, "_save", fail_save)
    try:
        event = Convention.from_api({"id": "x", "thumbnail": URL}).detail_view("Asia/Shanghai")
        prepared, resources = await cache.prepare({"event": event})
        poster = prepared["event"]["poster"]
        assert poster is not None
        assert poster["url"] in resources
        assert not list(tmp_path.glob("*.png"))
    finally:
        await cache.close()


async def test_cache_touch_failure_still_uses_valid_image_offline(tmp_path, monkeypatch):
    online = [True]
    calls = []

    def upstream(request):
        calls.append(request)
        if not online[0]:
            return httpx.Response(503)
        return httpx.Response(200, content=picture(), headers={"content-type": "image/png"})

    cache = ImageCache(tmp_path, transport=httpx.MockTransport(upstream))
    try:
        first = await cache.get(URL)
        online[0] = False

        def fail_touch(*_args, **_kwargs):
            raise PermissionError("read only")

        monkeypatch.setattr("furrycons.images.os.utime", fail_touch)
        second = await cache.get(URL)
        assert second.data == first.data
        assert len(calls) == 1
    finally:
        await cache.close()
