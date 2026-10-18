import json
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from test_original_files import file_app

from furrycons.themes import BUILTINS, ThemeStore


def theme_zip(tmp_path, identity="custom", *, manifest_changes=None, file_changes=None):
    files = {p.name: p.read_bytes() for p in (BUILTINS / "material").iterdir() if p.is_file()}
    manifest = json.loads(files["theme.json"])
    manifest["id"] = identity
    manifest["name"] = "自定义主题"
    manifest.update(manifest_changes or {})
    files["theme.json"] = json.dumps(manifest, ensure_ascii=False).encode()
    files.update(file_changes or {})
    target = tmp_path / f"{identity}.zip"
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            if content is not None:
                archive.writestr(name, content)
    return target


async def test_import_choose_both_outputs_and_reload_without_changing_default(tmp_path):
    archive = theme_zip(tmp_path)
    app = file_app(tmp_path)
    app.themes = ThemeStore(tmp_path)
    try:
        installed = await app.execute("主题导入", attachment=archive, is_admin=True)
        assert "已安装 local:custom" in installed.text and "默认主题未改变" in installed.text
        assert "local:custom" in (await app.execute("主题列表")).text
        await app.execute("详情 x --theme local:custom")
        assert app.renderer.details[-1]["theme_key"] == "local:custom"
        first = await app.execute("查询 --theme local:custom")
        assert app.renderer.lists[-1]["theme_key"] == "local:custom"
        await app.execute(f"翻页 {first.query_code} 1 --theme local:custom")
        assert app.renderer.lists[-1]["theme_key"] == "local:custom"
        await app.execute(f"单图 {first.query_code} --theme local:custom")
        assert app.renderer.lists[-1]["theme_key"] == "local:custom"
        await app.execute("查询")
        assert app.renderer.lists[-1]["theme_key"] == "builtin:material"
    finally:
        await app.close()
    app = file_app(tmp_path)
    app.themes = ThemeStore(tmp_path)
    try:
        assert "local:custom" in (await app.execute("主题列表")).text
        assert (await app.execute("详情 x --theme local:custom")).image
        collision = await app.execute("主题导入", attachment=archive, is_admin=True)
        assert "未覆盖" in collision.text
        assert (await app.execute("详情 x --theme local:custom")).image
    finally:
        await app.close()


@pytest.mark.parametrize("role", ["ordinary-user", "group-owner", "channel-admin"])
async def test_only_bot_admin_can_import_and_rejection_precedes_file_access(tmp_path, role):
    app = file_app(tmp_path)
    app.themes = ThemeStore(tmp_path)
    try:
        reply = await app.execute("主题导入", user=role, attachment=tmp_path / "nonexistent.zip")
        assert "仅机器人管理员" in reply.text
        assert "local:" not in (await app.execute("主题列表")).text
    finally:
        await app.close()


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"format_version": 2}, "格式版本"),
        ({"data_version": 2}, "数据版本"),
        ({"format_version": True}, "格式版本"),
        ({"compatibility": {"plugin_min": "2.0.0", "plugin_max_exclusive": "3.0.0"}}, "兼容区间"),
        ({"id": "../escape"}, "主题标识"),
        ({"templates": {"list": "list.html"}}, "模板"),
        ({"business_logic": "evil.py"}, "声明"),
    ],
)
async def test_invalid_package_contract_is_diagnostic_and_preserves_good_theme(
    tmp_path, changes, expected
):
    app = file_app(tmp_path)
    app.themes = ThemeStore(tmp_path)
    good = theme_zip(tmp_path, "good")
    bad = theme_zip(tmp_path, "bad", manifest_changes=changes)
    try:
        assert "已安装" in (await app.execute("主题导入", attachment=good, is_admin=True)).text
        rejected = await app.execute("主题导入", attachment=bad, is_admin=True)
        assert expected in rejected.text and "已安装" not in rejected.text
        assert (await app.execute("详情 x --theme local:good")).image
        assert "local:bad" not in (await app.execute("主题列表")).text
    finally:
        await app.close()


@pytest.mark.parametrize(
    "files,expected",
    [
        ({"../outside.txt": b"escape"}, "越界"),
        ({"C:/outside.txt": b"escape"}, "保留名称"),
        ({"NUL.txt": b"escape"}, "保留名称"),
        ({"main.py": b"print('business logic')"}, "非展示资源"),
        ({"detail.html": b"<html><head></head><body>No source</body></html>"}, "必需信息"),
        ({"detail.html": b"{{ cycler.__init__.__globals__ }}"}, "数据契约"),
        ({"detail.html": b"{{ 'x' * 999999999 }}"}, "运算表达式"),
        ({"detail.html": b"{% include '../other.html' %}"}, "越界"),
        ({"detail.html": b"{{ asset('missing.png') }}"}, "资源缺失"),
        ({"remote.css": b"@import 'https://example.com/fonts.css';"}, "外部地址"),
    ],
)
async def test_bad_paths_code_layout_and_missing_resources_are_rejected(tmp_path, files, expected):
    app = file_app(tmp_path)
    app.themes = ThemeStore(tmp_path)
    archive = theme_zip(tmp_path, file_changes=files)
    try:
        rejected = await app.execute("主题导入", attachment=archive, is_admin=True)
        assert expected in rejected.text and "已安装" not in rejected.text
        assert not (tmp_path / "outside.txt").exists()
        assert "local:custom" not in (await app.execute("主题列表")).text
    finally:
        await app.close()


async def test_symlink_and_case_collision_zip_entries_are_rejected(tmp_path):
    app = file_app(tmp_path)
    app.themes = ThemeStore(tmp_path)
    archive = theme_zip(tmp_path)
    info = ZipInfo("linked.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(archive, "a") as zip_file:
        zip_file.writestr(info, "../../private.txt")
    try:
        assert "链接" in (await app.execute("主题导入", attachment=archive, is_admin=True)).text
        archive = theme_zip(tmp_path, file_changes={"DETAIL.HTML": b"duplicate"})
        assert "重名" in (await app.execute("主题导入", attachment=archive, is_admin=True)).text
    finally:
        await app.close()


async def test_missing_unknown_or_unavailable_theme_does_not_silently_fall_back(tmp_path):
    app = file_app(tmp_path)
    app.themes = ThemeStore(tmp_path)
    try:
        assert "上传主题 ZIP" in (await app.execute("主题导入", is_admin=True)).text
        assert "主题用法" in (await app.execute("查询 --theme")).text
        assert "尚未安装" in (await app.execute("详情 x --theme local:missing")).text
        assert not app.renderer.lists and not app.renderer.details
    finally:
        await app.close()
