import pytest
from test_original_files import file_app
from test_themes import theme_zip

from furrycons.errors import UserError
from furrycons.themes import ThemeStore


def themed_app(tmp_path):
    app = file_app(tmp_path)
    app.themes = ThemeStore(tmp_path)
    return app


async def test_theme_precedence_single_use_and_import_do_not_change_defaults(tmp_path):
    app = themed_app(tmp_path)
    try:
        await app.execute("查询", chat="group")
        assert app.renderer.lists[-1]["theme_key"] == "builtin:material"
        assert (
            "已更新全局"
            in (await app.execute("主题默认 全局 builtin:liquidglass", is_admin=True)).text
        )
        await app.execute("查询", chat="group")
        assert app.renderer.lists[-1]["theme_key"] == "builtin:liquidglass"
        await app.execute("主题默认 本会话 builtin:material", chat="group", is_admin=True)
        archive = theme_zip(tmp_path)
        assert "已安装" in (await app.execute("主题导入", attachment=archive, is_admin=True)).text
        await app.execute("查询 --theme local:custom", chat="group", user="ordinary")
        assert app.renderer.lists[-1]["theme_key"] == "local:custom"
        await app.execute("详情 x", chat="group")
        assert app.renderer.details[-1]["theme_key"] == "builtin:material"
        view = (await app.execute("主题默认", chat="group")).text
        assert "全局默认：builtin:liquidglass" in view and "本会话默认：builtin:material" in view
        await app.execute("主题默认 本会话 清除", chat="group", is_admin=True)
        await app.execute("查询", chat="group")
        assert app.renderer.lists[-1]["theme_key"] == "builtin:liquidglass"
        await app.execute("主题默认 全局 清除", is_admin=True)
        await app.execute("查询", chat="group")
        assert app.renderer.lists[-1]["theme_key"] == "builtin:material"
    finally:
        await app.close()


async def test_session_defaults_persist_and_isolate_platform_group_channel_private(tmp_path):
    chats = [
        "one:GroupMessage:42",
        "one:FriendMessage:42",
        "one:ChannelMessage:42",
        "two:GroupMessage:42",
    ]
    app = themed_app(tmp_path)
    await app.execute("主题默认 全局 builtin:liquidglass", is_admin=True)
    await app.execute("主题默认 本会话 builtin:material", chat=chats[0], is_admin=True)
    await app.close()
    app = themed_app(tmp_path)
    try:
        for index, chat in enumerate(chats):
            await app.execute("查询", chat=chat, user="different-user")
            assert app.renderer.lists[-1]["theme_key"] == (
                "builtin:material" if index == 0 else "builtin:liquidglass"
            )
    finally:
        await app.close()


@pytest.mark.parametrize("role", ["ordinary", "group-owner", "channel-admin"])
async def test_default_management_rejects_non_bot_admin_for_chat_and_shared_boundary(
    tmp_path, role
):
    app = themed_app(tmp_path)
    try:
        for scope in ("全局", "本会话"):
            result = await app.execute(f"主题默认 {scope} builtin:liquidglass", user=role)
            assert "仅机器人管理员" in result.text
            result = await app.execute(f"主题默认 {scope} 清除", user=role)
            assert "仅机器人管理员" in result.text
        with pytest.raises(UserError, match="仅机器人管理员"):
            await app.manage_theme("set_default", scope="global", key="builtin:liquidglass")
        assert (await app.manage_theme("defaults"))["effective"] == "builtin:material"
    finally:
        await app.close()


async def test_missing_default_is_explicit_and_repair_does_not_require_it_to_render(tmp_path):
    app = themed_app(tmp_path)
    archive = theme_zip(tmp_path)
    try:
        await app.execute("主题导入", attachment=archive, is_admin=True)
        await app.execute("主题默认 本会话 local:custom", is_admin=True)
        first = await app.execute("详情 x")
        (tmp_path / "themes/custom/theme.json").unlink()
        missing = await app.execute("查询")
        assert (
            not missing.image
            and "local:custom 不可用" in missing.text
            and "--theme" in missing.text
        )
        assert not app.renderer.lists
        assert (await app.execute(f"原图 {first.output_code}")).file == first.image
        assert (await app.execute("详情 x --theme builtin:material")).image
        assert "尚未安装" in (await app.execute("主题默认 全局 local:unknown", is_admin=True)).text
        await app.execute("主题默认 本会话 清除", is_admin=True)
        await app.execute("查询")
        assert app.renderer.lists[-1]["theme_key"] == "builtin:material"
    finally:
        await app.close()
