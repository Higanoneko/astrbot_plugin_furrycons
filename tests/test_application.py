from pathlib import Path

import httpx
import pytest

from furrycons.app import Application
from furrycons.config import Settings
from furrycons.errors import RenderError
from furrycons.source import FurryConsSource


class CapturingRenderer:
    def __init__(self, failure=None):
        self.views = []
        self.failure = failure

    async def detail(self, view):
        if self.failure:
            raise self.failure
        self.views.append(view)
        return Path("generated-original.png")

    async def status(self):
        return "浏览器可用"

    async def close(self):
        pass


def make_app(handler, renderer=None):
    settings = Settings.from_dict({"api_key": "secret-test-token"})
    source = FurryConsSource(settings, httpx.MockTransport(handler))
    renderer = renderer or CapturingRenderer()
    return Application(settings, source, renderer)


async def test_detail_target_fields_timezone_and_original():
    def upstream(request):
        assert request.url.path == "/open/event/event-1"
        assert request.headers["Authorization"] == "secret-test-token"
        return httpx.Response(
            200,
            json={
                "id": "event-1",
                "name": "测试兽展",
                "status": "scheduled",
                "startDate": "2026-10-17T18:00:00Z",
                "endDate": "2026-10-18T10:00:00Z",
                "region": "广东省",
                "detail": "<script>原文不会执行</script>",
                "url": "https://www.furrycons.cn/test",
                "unknown_new_field": {"ok": True},
            },
        )

    app = make_app(upstream)
    try:
        result = await app.execute("详情 event-1")
        assert result.image == Path("generated-original.png")
        view = app.renderer.views[0]
        assert view["name"] == "测试兽展"
        assert view["start_label"] == "2026-10-18 02:00"
        assert view["address"] == "地址未明确"
        assert view["introduction_type"] == "原文节选"
        assert view["source"] == "https://www.furrycons.cn/test"
    finally:
        await app.close()


async def test_missing_fields_unknown_status_and_excerpt():
    app = make_app(
        lambda _: httpx.Response(
            200, json={"id": "x", "status": "new-state", "detail": "中" * 2000}
        )
    )
    try:
        await app.execute("详情 x")
        view = app.renderer.views[0]
        assert view["name"] == "名称未明确"
        assert view["region"] == "地区未明确"
        assert view["start_label"] == "日期未明确"
        assert "未知状态（new-state）" == view["status_label"]
        assert view["introduction"].startswith("中" * 1800)
        assert "更多内容" in view["introduction"]
    finally:
        await app.close()


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (404, {}, "未找到"),
        (401, {}, "鉴权失败"),
        (500, {"key": "secret-test-token"}, "暂不可用"),
        (200, {"id": "different"}, "身份不匹配"),
        (200, {}, "缺少展会身份"),
    ],
)
async def test_source_failure_is_not_a_detail_or_secret(status, payload, expected):
    app = make_app(lambda _: httpx.Response(status, json=payload))
    try:
        result = await app.execute("详情 x")
        assert result.image is None
        assert expected in result.text
        assert "secret-test-token" not in result.text
    finally:
        await app.close()


async def test_invalid_id_and_transport_failure():
    def unavailable(request):
        raise httpx.ConnectError("secret-test-token", request=request)

    app = make_app(unavailable)
    try:
        assert "ID 无效" in (await app.execute("详情 ../private")).text
        assert "暂不可用" in (await app.execute("详情 x")).text
    finally:
        await app.close()


async def test_browser_failure_and_deployment_status():
    app = make_app(
        lambda _: httpx.Response(200, json={"id": "x"}),
        CapturingRenderer(RenderError("浏览器不可用，请部署")),
    )
    try:
        assert "请部署" in (await app.execute("详情 x")).text
        assert (await app.execute("状态")).text == "浏览器可用"
    finally:
        await app.close()
