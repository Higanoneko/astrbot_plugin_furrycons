import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_theme_defaults import themed_app
from test_themes import theme_zip

from furrycons.errors import UserError
from furrycons.themes import ThemeStore


async def test_explicit_local_zip_update_retains_defaults_palette_and_backup(tmp_path):
    app = themed_app(tmp_path)
    try:
        await app.manage_theme("import", is_admin=True, path=theme_zip(tmp_path))
        await app.manage_theme(
            "set_colors",
            key="local:custom",
            changes={"primary": "#ab77ef", "mode": "dark"},
            is_admin=True,
        )
        await app.manage_theme("set_default", key="local:custom", scope="global", is_admin=True)
        reply = await app.execute(
            "主题更新 local:custom",
            attachment=theme_zip(tmp_path, manifest_changes={"version": "1.1.0"}),
            is_admin=True,
        )
        assert "已更新 local:custom" in reply.text
        assert app.themes.get("local:custom").manifest["version"] == "1.1.0"
        assert app.themes.get("local:custom").view()["colors"]["primary"] == "#ab77ef"
        assert app.themes.defaults("chat")["effective"] == "local:custom"
        backup = next(app.themes.local_dir.glob(".previous-*"))
        assert json.loads((backup / "theme.json").read_text(encoding="utf-8"))["version"] == "1.0.0"
    finally:
        await app.close()


async def test_updates_reject_bad_package_identity_builtin_and_non_admin(tmp_path):
    app = themed_app(tmp_path)
    try:
        await app.manage_theme("import", is_admin=True, path=theme_zip(tmp_path))
        with pytest.raises(UserError, match="标识"):
            await app.manage_theme(
                "update",
                key="local:custom",
                path=theme_zip(tmp_path, identity="other"),
                is_admin=True,
            )
        with pytest.raises(UserError, match="内置"):
            await app.manage_theme(
                "update", key="builtin:material", path=tmp_path / "absent.zip", is_admin=True
            )
        with pytest.raises(UserError, match="管理员"):
            await app.manage_theme("update", key="local:custom", path=tmp_path / "absent.zip")
        bad = tmp_path / "broken.zip"
        bad.write_bytes(b"not a zip")
        with pytest.raises(UserError, match="无法读取"):
            await app.manage_theme("update", key="local:custom", path=bad, is_admin=True)
        assert app.themes.get("local:custom").manifest["version"] == "1.0.0"
    finally:
        await app.close()


async def test_update_io_failure_restores_previous_package(tmp_path, monkeypatch):
    app = themed_app(tmp_path)
    try:
        await app.manage_theme("import", is_admin=True, path=theme_zip(tmp_path))
        original = Path.rename

        def fail_stage(path, target):
            if path.name.startswith(".import-"):
                raise PermissionError("fixture filesystem fault")
            return original(path, target)

        monkeypatch.setattr(Path, "rename", fail_stage)
        with pytest.raises(UserError):
            await app.manage_theme(
                "update",
                key="local:custom",
                path=theme_zip(tmp_path, manifest_changes={"version": "1.1.0"}),
                is_admin=True,
            )
        assert app.themes.get("local:custom").manifest["version"] == "1.0.0"
    finally:
        await app.close()
    restored = ThemeStore(tmp_path)
    assert restored.get("local:custom").manifest["version"] == "1.0.0"
    restored.close()


async def test_update_commit_failure_does_not_report_failure_after_applying(tmp_path, monkeypatch):
    app = themed_app(tmp_path)
    try:
        await app.manage_theme("import", is_admin=True, path=theme_zip(tmp_path))
        original = Path.unlink

        def fail_commit(path, *args, **kwargs):
            if path.name == ".pending-update.json":
                raise PermissionError("fixture journal cleanup fault")
            return original(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", fail_commit)
            with pytest.raises(UserError, match="原有主题保持可用"):
                await app.manage_theme(
                    "update",
                    key="local:custom",
                    is_admin=True,
                    path=theme_zip(tmp_path, manifest_changes={"version": "1.1.0"}),
                )
        assert app.themes.get("local:custom").manifest["version"] == "1.0.0"
    finally:
        await app.close()
    restored = ThemeStore(tmp_path)
    assert restored.get("local:custom").manifest["version"] == "1.0.0"
    restored.close()


def test_interrupted_directory_swap_recovers_exact_local_identity(tmp_path):
    themes = ThemeStore(tmp_path)
    themes.import_zip(theme_zip(tmp_path))
    token = uuid4().hex
    (themes.local_dir / "custom").rename(themes.local_dir / f".previous-custom-{token}")
    (themes.local_dir / ".pending-update.json").write_text(
        json.dumps({"id": "custom", "token": token}), encoding="utf-8"
    )
    themes.close()
    restored = ThemeStore(tmp_path)
    assert restored.get("local:custom").manifest["version"] == "1.0.0"
    assert not (restored.local_dir / ".pending-update.json").exists()
    restored.close()


async def test_interrupted_journal_write_leaves_old_theme_loadable(tmp_path, monkeypatch):
    app = themed_app(tmp_path)
    try:
        await app.manage_theme("import", is_admin=True, path=theme_zip(tmp_path))
        update = theme_zip(tmp_path, manifest_changes={"version": "1.1.0"})
        original = Path.write_text

        def interrupted(path, data, *args, **kwargs):
            if path.name in (".pending-update.json", ".pending-update.tmp"):
                original(path, '{"id":', encoding="utf-8")
                raise OSError("fixture interrupted write")
            return original(path, data, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "write_text", interrupted)
            with pytest.raises(UserError, match="原有主题保持可用"):
                await app.manage_theme("update", key="local:custom", path=update, is_admin=True)
        assert not (app.themes.local_dir / ".pending-update.json").exists()
    finally:
        await app.close()
    restored = ThemeStore(tmp_path)
    assert restored.get("local:custom").manifest["version"] == "1.0.0"
    restored.close()


async def test_structured_catalog_and_preview_share_chat_theme_decisions(tmp_path):
    app = themed_app(tmp_path)
    try:
        catalog = await app.manage_theme("catalog")
        assert {t["key"] for t in catalog} >= {"builtin:material", "builtin:liquidglass"}
        glass = next(t for t in catalog if t["key"] == "builtin:liquidglass")
        assert (
            glass["available"] and "background" in glass["advanced"] and glass["source"] == "内置"
        )
        with pytest.raises(UserError, match="管理员"):
            await app.manage_theme("preview", key="builtin:material", kind="list")
        await app.manage_theme("preview", key="builtin:liquidglass", kind="list", is_admin=True)
        data = app.renderer.lists[-1]
        assert "示例数据" in data["range"] and data["theme_key"] == "builtin:liquidglass"
        await app.manage_theme("preview", key="builtin:material", kind="detail", is_admin=True)
        assert "示例" in app.renderer.details[-1]["name"]
        with pytest.raises(UserError, match="预览类型"):
            await app.manage_theme(
                "preview", key="builtin:material", kind="../../other", is_admin=True
            )
    finally:
        await app.close()
