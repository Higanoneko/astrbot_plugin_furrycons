import asyncio
import sqlite3
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from test_query import Renderer, row

from furrycons.app import Application
from furrycons.config import Settings
from furrycons.outputs import OutputStore
from furrycons.source import FurryConsSource

ORIGINAL = b"generated-original-image\x00\xff\x01"


def file_app(tmp_path, now=None):
    now = now or [datetime.fromisoformat("2026-10-18T00:00:00Z")]

    class FileRenderer(Renderer):
        async def detail(self, view):
            self.details.append(view)
            return self.original()

        async def listing(self, view):
            self.lists.append(view)
            return self.original()

        def original(self):
            path = tmp_path / f"{uuid4().hex}.png"
            path.write_bytes(ORIGINAL)
            return path

    def upstream(request):
        event = row("x", detail="原文")
        if request.url.path.endswith("event/"):
            return httpx.Response(
                200, json={"data": [event], "current": 1, "pageSize": 1, "total": 1}
            )
        return httpx.Response(200, json=event)

    settings = Settings.from_dict({"api_key": "test"})
    return Application(
        settings,
        FurryConsSource(settings, httpx.MockTransport(upstream)),
        FileRenderer(),
        outputs=OutputStore(tmp_path / "outputs.db", tmp_path),
        clock=lambda: now[0],
    )


class Transport:
    def __init__(self, *, image_error=False, file_error=False, files=True):
        self.can_send_files = files
        self.image_error, self.file_error = image_error, file_error
        self.images, self.files = [], []

    async def send_image(self, path):
        self.images.append((path, path.read_bytes()))
        if self.image_error:
            raise RuntimeError("secret-image-error")
        return {"message_id": "outbound-image"}

    async def send_file(self, path):
        self.files.append((path, path.read_bytes()))
        if self.file_error:
            raise RuntimeError("secret-file-error")
        return {"message_id": "outbound-file"}


@pytest.mark.parametrize("command", ["详情 x", "查询"])
async def test_original_reference_identifies_exact_output_and_is_shared_only_within_chat(
    tmp_path, command
):
    app = file_app(tmp_path)
    try:
        reply = await app.execute(command, chat="group", user="a")
        assert reply.image and reply.output_code and f"原图 {reply.output_code}" in reply.text
        transport = Transport()
        await app.deliver(reply, transport, chat="group")
        assert transport.images == [(reply.image, ORIGINAL)] and not transport.files
        original = await app.execute(f"原图 {reply.output_code}", chat="group", user="b")
        assert original.file == reply.image and not original.image
        await app.deliver(original, transport, chat="group")
        assert transport.files == [(reply.image, ORIGINAL)]
        denied = await app.execute(f"原图 {reply.output_code}", chat="other", user="a")
        assert denied.file is None and "当前聊天中没有该输出" in denied.text
    finally:
        await app.close()


async def test_original_file_survives_reload_without_extending_expiry(tmp_path):
    now = [datetime.fromisoformat("2026-10-18T00:00:00Z")]
    app = file_app(tmp_path, now)
    first = await app.execute("详情 x", chat="group")
    await app.close()
    app = file_app(tmp_path, now)
    try:
        now[0] += timedelta(hours=24, microseconds=-1)
        assert (await app.execute(f"原图 {first.output_code}", chat="group")).file == first.image
        now[0] += timedelta(microseconds=1)
        expired = await app.execute(f"原图 {first.output_code}", chat="group")
        assert not expired.file and "已过期" in expired.text
    finally:
        await app.close()


@pytest.mark.parametrize("damage", ["missing", "changed"])
async def test_lost_or_modified_file_is_not_silently_regenerated(tmp_path, damage):
    app = file_app(tmp_path)
    try:
        first = await app.execute("详情 x")
        if damage == "missing":
            first.image.unlink()
        else:
            first.image.write_bytes(b"a different image")
        reply = await app.execute(f"原图 {first.output_code}")
        assert not reply.file and "重新查询" in reply.text
        assert len(app.renderer.details) == 1
    finally:
        await app.close()


@pytest.mark.parametrize(
    "files,file_error,expected",
    [(True, False, "同一原文件"), (True, True, "均发送失败"), (False, False, "未声明附件能力")],
)
async def test_image_failure_attempts_same_file_only_when_supported(
    tmp_path, files, file_error, expected
):
    app = file_app(tmp_path)
    try:
        reply = await app.execute("查询", chat="group")
        transport = Transport(image_error=True, files=files, file_error=file_error)
        messages = await app.deliver(reply, transport, chat="group")
        assert any(expected in message for message in messages)
        assert transport.images == [(reply.image, ORIGINAL)]
        assert transport.files == ([(reply.image, ORIGINAL)] if files else [])
        assert "secret-" not in "".join(messages)
        # A file fallback receipt cannot associate a quoted list image.
        unbound = await app.execute("1", chat="group", quote_id="outbound-file")
        assert "无法关联" in unbound.text
    finally:
        await app.close()


@pytest.mark.parametrize("files", [True, False])
async def test_explicit_file_failure_and_unsupported_capability_are_visible(tmp_path, files):
    app = file_app(tmp_path)
    try:
        first = await app.execute("详情 x")
        original = await app.execute(f"原文件 {first.output_code}")
        transport = Transport(files=files, file_error=True)
        messages = await app.deliver(original, transport, chat="local")
        assert any(("附件发送失败" if files else "未声明原文件附件能力") in m for m in messages)
        assert "secret-" not in "".join(messages)
    finally:
        await app.close()
@pytest.mark.parametrize("files", [True, False])
async def test_sent_image_is_not_sent_again_when_reference_storage_fails(tmp_path, files):
    app = file_app(tmp_path)
    try:
        reply = await app.execute("查询", chat="group")
        app.snapshots.connection.set_authorizer(
            lambda action, table, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and table == "replies"
            else sqlite3.SQLITE_OK
        )
        transport = Transport(files=files)
        messages = await app.deliver(reply, transport, chat="group")
        text = "\n".join(messages)
        assert transport.images == [(reply.image, ORIGINAL)] and transport.files == []
        assert "引用关联" in text and reply.query_code in text
        assert "图片发送失败" not in text
        assert f"选择 {reply.query_code}" in text and "翻页" in text
    finally:
        await app.close()


async def test_cancelled_image_send_propagates_without_attachment_fallback(tmp_path):
    app = file_app(tmp_path)
    try:
        reply = await app.execute("查询", chat="group")
        transport = Transport()

        async def cancelled(_path):
            raise asyncio.CancelledError

        transport.send_image = cancelled
        with pytest.raises(asyncio.CancelledError):
            await app.deliver(reply, transport, chat="group")
        assert transport.files == [] and app._sending_paths == {}
    finally:
        await app.close()
