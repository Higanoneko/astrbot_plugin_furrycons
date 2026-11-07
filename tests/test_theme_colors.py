import json
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from test_theme_defaults import themed_app
from test_themes import theme_zip

from furrycons.errors import UserError


@pytest.mark.parametrize("key", ["builtin:material", "builtin:liquidglass"])
async def test_color_changes_are_independent_and_survive_reload(tmp_path, key):
    app = themed_app(tmp_path)
    assert "已保存" in (await app.execute(f"主题改色 {key} 明暗 dark", is_admin=True)).text
    await app.execute(f"主题改色 {key} 主色 #AB77EF", is_admin=True)
    await app.execute(f"主题改色 {key} background #131722", is_admin=True)
    await app.close()
    app = themed_app(tmp_path)
    try:
        palette = await app.manage_theme("palette", key=key)
        assert palette["mode"] == "dark"
        assert palette["colors"]["primary"] == "#ab77ef"
        assert palette["colors"]["background"] == "#131722"
        text = (await app.execute(f"主题配色 {key}")).text
        assert "明暗 dark" in text and "#ab77ef" in text
        await app.execute(f"详情 x --theme {key}")
        assert app.renderer.details[-1]["theme_key"] == key
        await app.execute(f"查询 --theme {key}")
        assert app.renderer.lists[-1]["theme_key"] == key
        await app.execute(f"主题改色 {key} 重置", is_admin=True)
        reset = await app.manage_theme("palette", key=key)
        assert reset["mode"] == "light" and reset["colors"]["primary"] != "#ab77ef"
    finally:
        await app.close()


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("主色", "red", "六位十六进制"),
        ("明暗", "auto", "light 或 dark"),
        ("background", "url(secret)", "CSS 或布局"),
        ("font-size", "#ffffff", "未声明"),
    ],
)
async def test_invalid_color_patch_preserves_existing_settings(tmp_path, field, value, expected):
    app = themed_app(tmp_path)
    key = "builtin:material"
    try:
        await app.execute(f"主题改色 {key} 主色 #aa55ff", is_admin=True)
        previous = await app.manage_theme("palette", key=key)
        result = await app.execute(f"主题改色 {key} {field} {value}", is_admin=True)
        assert expected in result.text
        assert await app.manage_theme("palette", key=key) == previous
        with pytest.raises(UserError, match="未声明"):
            await app.manage_theme(
                "set_colors",
                is_admin=True,
                key=key,
                changes={"primary": "#123456", "advanced": {"spacing": "#ffffff"}},
            )
        assert await app.manage_theme("palette", key=key) == previous
    finally:
        await app.close()


async def test_local_copy_clones_colors_then_remains_independent_of_source(tmp_path):
    app = themed_app(tmp_path)
    await app.execute("主题改色 builtin:liquidglass 主色 #AA77EF", is_admin=True)
    await app.execute("主题改色 builtin:liquidglass 明暗 dark", is_admin=True)
    copied = await app.execute("主题副本 builtin:liquidglass purple-glass", is_admin=True)
    assert "local:purple-glass" in copied.text
    await app.execute("主题改色 builtin:liquidglass 重置", is_admin=True)
    await app.execute("主题默认 本会话 local:purple-glass", chat="group", is_admin=True)
    await app.close()
    app = themed_app(tmp_path)
    try:
        palette = await app.manage_theme("palette", key="local:purple-glass")
        assert palette["origin"] == "builtin:liquidglass" and palette["mode"] == "dark"
        assert palette["colors"]["primary"] == "#aa77ef"
        assert "本地副本" in palette["name"]
        original = await app.manage_theme("palette", key="builtin:liquidglass")
        assert original["mode"] == "light" and original["colors"]["primary"] != "#aa77ef"
        assert (
            "未覆盖"
            in (await app.execute("主题副本 builtin:material purple-glass", is_admin=True)).text
        )
        await app.execute("查询", chat="group")
        assert app.renderer.lists[-1]["theme_key"] == "local:purple-glass"
    finally:
        await app.close()


async def test_export_edit_and_import_layout_without_overwriting_original(tmp_path):
    app = themed_app(tmp_path)
    try:
        exported = await app.execute("主题导出 builtin:material", is_admin=True)
        assert exported.file and exported.file.suffix == ".zip"
        with ZipFile(exported.file) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(files["theme.json"])
        manifest.update(id="layout-copy", name="布局副本", version="1.1.0")
        files["theme.json"] = json.dumps(manifest).encode()
        files["detail.html"] = files["detail.html"].replace(
            b'<body class="detail-page">', '<body class="detail-page"><p>本地排版副本</p>'.encode()
        )
        edited = tmp_path / "edited.zip"
        with ZipFile(edited, "w", ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        assert (
            "已安装 local:layout-copy"
            in (await app.execute("主题导入", attachment=edited, is_admin=True)).text
        )
        assert (await app.execute("详情 x --theme local:layout-copy")).image
        original = await app.execute("主题导出 builtin:material", is_admin=True)
        with ZipFile(original.file) as archive:
            assert "本地排版副本".encode() not in archive.read("detail.html")
            assert json.loads(archive.read("theme.json"))["id"] == "material"
        copy = await app.execute("主题导出 local:layout-copy", is_admin=True)
        with ZipFile(copy.file) as archive:
            assert "本地排版副本".encode() in archive.read("detail.html")
    finally:
        await app.close()


async def test_color_settings_are_not_bound_to_package_version(tmp_path):
    app = themed_app(tmp_path)
    archive = theme_zip(tmp_path, "versioned")
    await app.execute("主题导入", attachment=archive, is_admin=True)
    await app.execute("主题改色 local:versioned 主色 #aabbcc", is_admin=True)
    await app.close()
    # Replace package metadata as an external upgrade would; preferences are separate.
    path = tmp_path / "themes/versioned/theme.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["version"] = "1.1.0"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    app = themed_app(tmp_path)
    try:
        palette = await app.manage_theme("palette", key="local:versioned")
        assert palette["version"] == "1.1.0" and palette["colors"]["primary"] == "#aabbcc"
    finally:
        await app.close()


@pytest.mark.parametrize("role", ["ordinary", "group-owner", "channel-admin"])
async def test_color_copy_and_export_writes_require_bot_admin_in_all_entries(tmp_path, role):
    app = themed_app(tmp_path)
    try:
        for command in (
            "主题改色 builtin:material 主色 #ff0000",
            "主题改色 builtin:material 重置",
            "主题副本 builtin:material illegal",
            "主题导出 builtin:material",
        ):
            assert "仅机器人管理员" in (await app.execute(command, user=role)).text
        for action, options in (
            ("set_colors", {"changes": {"mode": "dark"}}),
            ("copy", {"identity": "illegal"}),
            ("export", {}),
            ("reset_colors", {}),
        ):
            with pytest.raises(UserError, match="仅机器人管理员"):
                await app.manage_theme(action, key="builtin:material", **options)
        assert "local:illegal" not in (await app.execute("主题列表")).text
    finally:
        await app.close()
