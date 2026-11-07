"""Exercise browser process failures without networking or launching Chromium."""

import asyncio
import struct
from pathlib import Path

import pytest
from playwright.async_api import Error

from furrycons import render
from furrycons.config import Settings
from furrycons.errors import RenderError
from furrycons.preview import preview_data

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1080, 800)


class Page:
    async def set_content(self, *_args, **_kwargs):
        pass

    async def evaluate(self, expression):
        if "width: window.innerWidth" in expression:
            return {"width": 1080, "height": 800}
        return "f.family === 'FurryCons'" in expression

    async def screenshot(self, *, path, **_kwargs):
        Path(path).write_bytes(PNG)


class Context:
    def __init__(self, failure="", browser=None):
        self.failure, self.closes = failure, 0
        self.browser = browser
        self.page = Page()

    async def new_page(self):
        if self.failure == "new_page":
            self.browser.connected = False
            raise Error("secret-browser-error")
        if self.failure == "cancel":
            raise asyncio.CancelledError
        return self.page

    async def route(self, *_args):
        pass

    async def close(self):
        self.closes += 1
        if self.failure in ("context_close", "cancel"):
            raise Error("secret-cleanup-error")


class Browser:
    def __init__(self, failure=""):
        self.failure, self.connected, self.closes = failure, True, 0
        self.contexts = []

    def is_connected(self):
        return self.connected

    async def new_context(self, **_kwargs):
        if self.failure == "new_context":
            self.connected = False
            raise Error("secret-browser-error")
        context = Context(self.failure, self)
        self.contexts.append(context)
        return context

    async def close(self):
        self.closes += 1
        self.connected = False


class Engine:
    def __init__(self, browsers):
        self.chromium = self
        self.browsers, self.launches, self.stops = browsers, 0, 0

    async def launch(self, **_kwargs):
        browser = self.browsers[self.launches]
        self.launches += 1
        return browser

    async def stop(self):
        self.stops += 1


def renderer_fixture(tmp_path, monkeypatch, failure):
    browsers = [Browser(failure), Browser()]
    engine = Engine(browsers)

    class Manager:
        async def start(self):
            return engine

    monkeypatch.setattr(render, "async_playwright", Manager)
    return render.BrowserRenderer(Settings(), tmp_path / "images"), engine, browsers


@pytest.mark.parametrize("failure", ["new_context", "new_page", "context_close"])
async def test_browser_failure_is_actionable_and_next_request_launches_fresh_browser(
    tmp_path, monkeypatch, failure,
):
    renderer, engine, browsers = renderer_fixture(tmp_path, monkeypatch, failure)
    try:
        with pytest.raises(RenderError) as caught:
            await renderer.detail(preview_data()["event"])
        assert "浏览器" in str(caught.value) and "重试" in str(caught.value)
        assert "secret-" not in str(caught.value)
        assert browsers[0].closes == 1
        assert all(context.closes == 1 for context in browsers[0].contexts)
        assert not list(renderer.output_dir.glob("*.png"))
        path = await renderer.detail(preview_data()["event"])
        assert path.read_bytes() == PNG and engine.launches == 2
        assert browsers[1].contexts[0].closes == 1
    finally:
        await renderer.close()
    assert engine.stops == 1 and browsers[1].closes == 1


async def test_context_cleanup_error_does_not_replace_cancellation(tmp_path, monkeypatch):
    renderer, _engine, browsers = renderer_fixture(tmp_path, monkeypatch, "cancel")
    try:
        with pytest.raises(asyncio.CancelledError):
            await renderer.detail(preview_data()["event"])
        assert browsers[0].contexts[0].closes == 1
        path = await renderer.detail(preview_data()["event"])
        assert path.read_bytes() == PNG
    finally:
        await renderer.close()


async def test_driver_start_failure_is_user_diagnostic_and_retry_starts_driver(
    tmp_path, monkeypatch,
):
    renderer, engine, _browsers = renderer_fixture(tmp_path, monkeypatch, "")
    attempts = []

    class Manager:
        async def start(self):
            attempts.append(True)
            if len(attempts) == 1:
                raise Error("secret-driver-error")
            return engine

    monkeypatch.setattr(render, "async_playwright", Manager)
    try:
        with pytest.raises(RenderError, match="浏览器"):
            await renderer.detail(preview_data()["event"])
        assert renderer._engine is None
        assert (await renderer.detail(preview_data()["event"])).read_bytes() == PNG
        assert len(attempts) == 2
    finally:
        await renderer.close()


async def test_cancelled_screenshot_closes_context_and_removes_partial_file(tmp_path, monkeypatch):
    renderer, engine, browsers = renderer_fixture(tmp_path, monkeypatch, "")
    started = asyncio.Event()
    original = Page.screenshot

    async def blocked(_page, *, path, **_kwargs):
        Path(path).write_bytes(b"partial PNG")
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(Page, "screenshot", blocked)
    task = asyncio.create_task(renderer.detail(preview_data()["event"]))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert browsers[0].contexts[0].closes == 1
        assert not list(renderer.output_dir.glob("*.png"))
        monkeypatch.setattr(Page, "screenshot", original)
        assert (await renderer.detail(preview_data()["event"])).read_bytes() == PNG
        assert engine.launches == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await renderer.close()


async def test_shutdown_stops_driver_even_when_browser_close_fails(tmp_path, monkeypatch):
    renderer, engine, _browsers = renderer_fixture(tmp_path, monkeypatch, "")
    await renderer.detail(preview_data()["event"])

    async def failed_close():
        raise Error("secret-shutdown-error")

    monkeypatch.setattr(renderer._browser, "close", failed_close)
    await renderer.close()
    assert engine.stops == 1 and renderer._engine is None and renderer._browser is None


async def test_cancellation_during_context_close_discards_browser_and_unsubmitted_file(
    tmp_path, monkeypatch,
):
    renderer, engine, browsers = renderer_fixture(tmp_path, monkeypatch, "")
    started = asyncio.Event()
    original = Context.close

    async def blocked(context):
        context.closes += 1
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(Context, "close", blocked)
    task = asyncio.create_task(renderer.detail(preview_data()["event"]))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert browsers[0].closes == 1 and browsers[0].contexts[0].closes == 1
        assert not list(renderer.output_dir.glob("*.png"))
        monkeypatch.setattr(Context, "close", original)
        assert (await renderer.detail(preview_data()["event"])).read_bytes() == PNG
        assert engine.launches == 2
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await renderer.close()


async def test_page_failure_does_not_close_a_connected_shared_browser(tmp_path, monkeypatch):
    renderer, engine, browsers = renderer_fixture(tmp_path, monkeypatch, "")
    original = Page.screenshot

    async def failed(_page, **_kwargs):
        raise Error("secret-page-timeout")

    monkeypatch.setattr(Page, "screenshot", failed)
    try:
        with pytest.raises(RenderError, match="重试"):
            await renderer.detail(preview_data()["event"])
        assert browsers[0].closes == 0 and browsers[0].contexts[0].closes == 1
        monkeypatch.setattr(Page, "screenshot", original)
        assert (await renderer.detail(preview_data()["event"])).read_bytes() == PNG
        assert engine.launches == 1
    finally:
        await renderer.close()
