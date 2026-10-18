import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_application import make_app

DESCRIPTION = "这是一场兽迷交流活动。\n活动设有作品展示区。\n购票安排请以官网后续公告为准。"


def summary_app(output=None, error=None):
    app = make_app(
        lambda _: httpx.Response(
            200,
            json={"id": "x", "detail": DESCRIPTION, "url": "https://www.furrycons.cn/x"},
        )
    )
    app.settings = replace(app.settings, ai_summary=True, summary_provider_id="existing-model")

    async def model(provider_id, source):
        assert provider_id == "existing-model" and source == DESCRIPTION
        if error:
            raise error
        return output

    app.model = model
    return app


async def test_enabled_summary_selects_source_text_and_preserves_unknown_fields_and_source():
    excerpts = ["这是一场兽迷交流活动。", "活动设有作品展示区。"]
    app = summary_app(json.dumps(excerpts))
    try:
        reply = await app.execute("详情 x")
        assert reply.image
        view = app.renderer.views[-1]
        assert view["introduction_type"] == "AI 总结"
        assert view["introduction"] == "\n\n".join(excerpts)
        assert view["address"] == "地址未明确"
        assert view["source"] == "https://www.furrycons.cn/x"
    finally:
        await app.close()


@pytest.mark.parametrize("disabled", ["switch", "provider", "adapter"])
async def test_disabled_or_missing_model_uses_excerpt_without_calling_model(disabled):
    app = summary_app()
    calls = []

    async def model(*args):
        calls.append(args)
        return "[]"

    app.model = model
    if disabled == "switch":
        app.settings = replace(app.settings, ai_summary=False)
    elif disabled == "provider":
        app.settings = replace(app.settings, summary_provider_id="")
    else:
        app.model = None
    try:
        assert (await app.execute("详情 x")).image
        view = app.renderer.views[-1]
        assert view["introduction_type"] == "原文节选"
        assert view["introduction"] == DESCRIPTION
        assert not calls
    finally:
        await app.close()


@pytest.mark.parametrize(
    "output",
    [
        None,
        "",
        " ",
        "not-json",
        "[]",
        "{}",
        "[null]",
        '["门票免费。"]',  # A plausible, invented fact must not become event content.
        '["活动设有作品展示区。", "这是一场兽迷交流活动。"]',
        '["这是一场兽迷交流活动。", "这是一场兽迷交流活动。"]',
    ],
)
async def test_unusable_or_invented_summary_falls_back_without_losing_detail(output):
    app = summary_app(output)
    try:
        assert (await app.execute("详情 x")).image
        view = app.renderer.views[-1]
        assert view["introduction_type"] == "原文节选" and view["introduction"] == DESCRIPTION
        assert view["source"] == "https://www.furrycons.cn/x"
    finally:
        await app.close()


async def test_model_error_does_not_expose_provider_error_or_break_detail():
    app = summary_app(error=RuntimeError("secret-provider-key"))
    try:
        reply = await app.execute("详情 x")
        assert reply.image and "secret-provider-key" not in reply.text
        assert app.renderer.views[-1]["introduction_type"] == "原文节选"
    finally:
        await app.close()


async def test_model_timeout_falls_back_to_readable_excerpt():
    app = summary_app()
    app.settings = replace(app.settings, summary_timeout=1)

    async def stalled(*_):
        await asyncio.Event().wait()

    app.model = stalled
    try:
        reply = await app.execute("详情 x")
        assert reply.image
        view = app.renderer.views[-1]
        assert view["introduction_type"] == "原文节选" and view["introduction"] == DESCRIPTION
    finally:
        await app.close()
