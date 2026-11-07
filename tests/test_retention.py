import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_original_files import file_app

from furrycons.errors import UserError
from furrycons.outputs import OutputStore
from furrycons.query import SnapshotStore

NOW = datetime(2026, 10, 18, tzinfo=UTC)
GRACE = timedelta(hours=24)


def test_output_cleanup_boundary_reload_shared_and_unknown_files(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    old = root / "old.png"
    shared = root / "shared.png"
    unknown = root / "unknown.png"
    for path in (old, shared, unknown):
        path.write_bytes(b"image")
    db = tmp_path / "outputs.db"
    store = OutputStore(db, root)
    expired = store.register(old, "group", NOW, 60)
    old_shared = store.register(shared, "group", NOW, 60)
    active = store.register(shared, "group", NOW, 3 * 86400)
    store.close()
    store = OutputStore(db, root)
    try:
        deadline = NOW + timedelta(seconds=60) + GRACE
        assert store.prune(deadline - timedelta(microseconds=1)) == 0
        assert store.prune(deadline) == 2
        assert not old.exists() and shared.exists() and unknown.exists()
        assert store.get(active, "group", deadline) == shared.resolve()
        for code in (expired, old_shared):
            with pytest.raises(UserError, match="重新查询"):
                store.get(code, "group", deadline)
    finally:
        store.close()


def test_output_cleanup_protects_external_symlink_and_failed_delete(tmp_path, monkeypatch):
    root = tmp_path / "images"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    failed = root / "failed.png"
    failed.write_bytes(b"image")
    store = OutputStore(tmp_path / "outputs.db", root)
    failed_code = store.register(failed, "group", NOW, 1)
    with store.connection:
        store.connection.execute("INSERT INTO outputs VALUES (?, ?, ?, ?, ?)",
                                 ("EXTERNAL", "group", str(outside), None, NOW.timestamp() + 1))
    link = root / "link.png"
    try:
        link.symlink_to(outside)
    except OSError:
        link = None
    if link:
        with store.connection:
            store.connection.execute("INSERT INTO outputs VALUES (?, ?, ?, ?, ?)",
                                     ("LINK", "group", str(link), None, NOW.timestamp() + 1))
    real_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == failed:
            raise PermissionError("in use")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    try:
        assert store.prune(NOW + GRACE + timedelta(seconds=1)) == 0
        assert failed.exists() and outside.read_bytes() == b"outside"
        assert store.connection.execute("SELECT 1 FROM outputs WHERE code=?", (failed_code,)).fetchone()
        if link:
            assert link.is_symlink()
        monkeypatch.setattr(Path, "unlink", real_unlink)
        assert store.prune(NOW + GRACE + timedelta(seconds=1)) == 1
        assert not failed.exists()
    finally:
        store.close()


def test_output_cleanup_does_not_unlink_without_image_root(tmp_path):
    path = tmp_path / "result.png"
    path.write_bytes(b"image")
    store = OutputStore(tmp_path / "outputs.db")
    try:
        store.register(path, "group", NOW, 1)
        assert store.prune(NOW + GRACE + timedelta(seconds=1)) == 1
        assert path.exists()
    finally:
        store.close()


def test_snapshots_cleanup_removes_associations_only_after_grace_and_is_bounded(tmp_path):
    db = tmp_path / "snapshots.db"
    store = SnapshotStore(db)
    old = store.create("group", "a", [], "old", 1, NOW, 60)
    live = store.create("group", "a", [], "live", 1, NOW, 3 * 86400)
    store.bind_message("group", "old-message", old.code, 1)
    store.remember_page("group", "a", old.code, 1)
    store.bind_message("group", "live-message", live.code, 1)
    store.close()
    store = SnapshotStore(db)
    try:
        deadline = NOW + timedelta(seconds=60) + GRACE
        assert store.prune(deadline - timedelta(microseconds=1)) == 0
        assert store.prune(deadline, limit=1) == 1
        assert store.get(live.code, "group", deadline).code == live.code
        with pytest.raises(UserError, match="重新"):
            store.get(old.code, "group", deadline)
        assert not store.connection.execute("SELECT 1 FROM replies WHERE code=?", (old.code,)).fetchone()
        assert not store.connection.execute("SELECT 1 FROM page_views WHERE code=?", (old.code,)).fetchone()
        assert store.connection.execute("SELECT 1 FROM replies WHERE code=?", (live.code,)).fetchone()
    finally:
        store.close()


async def test_failed_maintenance_does_not_block_query_and_is_throttled(tmp_path, monkeypatch, caplog):
    now = [NOW]
    app = file_app(tmp_path, now)
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError("maintenance unavailable")

    monkeypatch.setattr(app.outputs, "prune", fail)
    try:
        assert (await app.execute("查询")).image
        assert (await app.execute("查询")).image
        assert len(calls) == 1 and "维护失败" in caplog.text
        now[0] += timedelta(hours=1)
        assert (await app.execute("查询")).image
        assert len(calls) == 2
    finally:
        await app.close()


async def test_file_being_sent_is_not_reclaimed_until_send_finishes(tmp_path):
    now = [NOW]
    app = file_app(tmp_path, now)
    sending, finish = asyncio.Event(), asyncio.Event()

    class SlowTransport:
        can_send_files = True

        async def send_file(self, path):
            sending.set()
            await finish.wait()
            assert path.exists()

    task = None
    try:
        result = await app.execute("详情 x")
        original = await app.execute(f"原图 {result.output_code}")
        task = asyncio.create_task(app.deliver(original, SlowTransport(), chat="local"))
        await sending.wait()
        now[0] += timedelta(hours=48)
        await app.execute("帮助")
        assert result.image.exists()
        finish.set()
        await task
        now[0] += timedelta(hours=1)
        await app.execute("帮助")
        assert not result.image.exists()
    finally:
        finish.set()
        if task:
            await task
        await app.close()


def test_cleanup_processes_at_most_the_batch_size(tmp_path):
    store = OutputStore(tmp_path / "outputs.db")
    try:
        with store.connection:
            store.connection.executemany(
                "INSERT INTO outputs VALUES (?, ?, ?, ?, ?)",
                [(str(index), "chat", "unused", None, NOW.timestamp()) for index in range(101)],
            )
        assert store.prune(NOW + GRACE) == 100
        assert store.connection.execute("SELECT COUNT(*) FROM outputs").fetchone()[0] == 1
        assert store.prune(NOW + GRACE) == 1
    finally:
        store.close()


def test_blocked_old_records_do_not_starve_later_records(tmp_path, monkeypatch, caplog):
    caplog.set_level("CRITICAL")
    root = tmp_path / "images"
    root.mkdir()
    blocked = root / "blocked.png"
    protected = root / "protected.png"
    target = root / "target.png"
    for path in (blocked, protected, target):
        path.write_bytes(b"image")
    store = OutputStore(tmp_path / "outputs.db", root)
    with store.connection:
        rows = []
        for index in range(100):
            path = tmp_path / "outside.png" if index < 34 else protected if index < 67 else blocked
            rows.append((f"{index:03d}", "chat", str(path), None, NOW.timestamp()))
        rows.append(("100", "chat", str(target), None, NOW.timestamp()))
        store.connection.executemany("INSERT INTO outputs VALUES (?, ?, ?, ?, ?)", rows)
    real_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("in use")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    try:
        assert store.prune(NOW + GRACE, protected={str(protected.resolve())}) == 0
        assert store.prune_pending and target.exists()
        assert store.prune(NOW + GRACE, protected={str(protected.resolve())}) == 1
        assert not store.prune_pending and not target.exists()
        monkeypatch.setattr(Path, "unlink", real_unlink)
        assert store.prune(NOW + GRACE) == 66
        assert not blocked.exists() and not protected.exists()
    finally:
        store.close()


async def test_backlog_continues_on_subsequent_commands_without_waiting_an_hour(tmp_path, monkeypatch):
    now = [NOW]
    app = file_app(tmp_path, now)
    calls = []
    original = app.outputs.prune

    def observe(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.outputs, "prune", observe)
    with app.outputs.connection:
        app.outputs.connection.executemany(
            "INSERT INTO outputs VALUES (?, ?, ?, ?, ?)",
            [(f"{index:03d}", "chat", str(tmp_path / "absent.png"), None,
              (NOW - GRACE).timestamp()) for index in range(201)],
        )
    with app.snapshots.connection:
        app.snapshots.connection.executemany(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
            [(f"{index:03d}", "chat", "user", "{}", 0, (NOW - GRACE).timestamp())
             for index in range(201)],
        )
    try:
        for remaining in (101, 1, 0):
            assert (await app.execute("帮助")).text
            assert app.outputs.connection.execute("SELECT COUNT(*) FROM outputs").fetchone()[0] == remaining
            assert app.snapshots.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == remaining
        assert len(calls) == 3
        await app.execute("帮助")
        assert len(calls) == 3
    finally:
        await app.close()
