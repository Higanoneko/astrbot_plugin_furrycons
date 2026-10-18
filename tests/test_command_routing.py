import pytest
from test_bare_interaction import build
from test_query import app_for, row

from furrycons.commands import CommandRoutes
from furrycons.config import Settings
from furrycons.errors import UserError
from furrycons.query import SnapshotStore


async def test_default_main_entry_and_independent_query_shortcut_share_query_behavior():
    app = build()
    try:
        first = await app.dispatch("FEC 查询")
        assert first.image and "查询短码" in first.text
        data = app.renderer.lists[-1]
        shortcut = await app.dispatch("查询展会")
        assert shortcut.image and "查询短码" in shortcut.text
        assert app.renderer.lists[-1]["events"] == data["events"]
        assert app.renderer.lists[-1]["range"] == data["range"]
    finally:
        await app.close()


async def test_custom_entry_preserves_function_aliases_permissions_and_command_hints():
    settings = Settings.from_dict(
        {
            "api_key": "fixture",
            "command_entry": "兽展",
            "function_aliases": ["查展=查询", "找展=搜索", "展会详情=详情", "设置主题=主题默认"],
        }
    )
    app = build(settings)
    try:
        assert not (await app.dispatch("FEC 查询")).image
        result = await app.dispatch("查展")
        assert result.image and "/兽展 原图" in result.text and "/FEC" not in result.text
        assert app.renderer.lists[-1]["command_entry"] == "兽展"
        assert (await app.dispatch("展会详情 test-id")).image
        assert app.renderer.details[-1]["id"] == "test-id"
        assert "请提供名称：/兽展 搜索" in (await app.dispatch("找展")).text
        assert "仅机器人管理员" in (await app.dispatch("设置主题 全局 builtin:liquidglass")).text
        assert (
            "已更新全局"
            in (await app.dispatch("设置主题 全局 builtin:liquidglass", is_admin=True)).text
        )
        assert "/兽展 查询" in (await app.execute("翻页 BAD 1")).text
        assert "/兽展 详情" in (await app.dispatch("展会详情 ../bad")).text
        assert "/兽展 原图" in (await app.execute("原图 BAD")).text
    finally:
        await app.close()


@pytest.mark.parametrize(
    "config",
    [
        {"command_entry": "/FEC"},
        {"command_entry": "two words"},
        {"function_aliases": ["FEC=查询"]},
        {"function_aliases": ["查展=查询", "查展=搜索"]},
        {"function_aliases": ["查展=不存在"]},
        {"function_aliases": ["查展"]},
    ],
)
def test_invalid_or_ambiguous_command_config_is_rejected(config):
    with pytest.raises(UserError):
        Settings.from_dict(config)


def test_existing_framework_root_and_alias_conflicts_are_rejected():
    routes = CommandRoutes(
        Settings.from_dict({"command_entry": "展会", "function_aliases": ["help=查询"]})
    )
    with pytest.raises(UserError, match="占用：help"):
        routes.validate_occupied({"help", "other"})
    with pytest.raises(UserError, match="占用：展会"):
        routes.validate_occupied({"展会"})
    routes.validate_occupied({"other", "another group subcommand"})


async def test_next_previous_pages_are_per_user_and_survive_store_reopen(tmp_path):
    app = app_for([row(i) for i in range(4)], page_size=1)
    app.snapshots.close()
    app.snapshots = SnapshotStore(tmp_path / "state.db")
    try:
        first = await app.execute("查询", chat="group", user="alice")
        code = first.query_code
        app.record_delivery(first, chat="group", message_id="page-one")
        await app.execute(f"下一页 {code}", chat="group", user="alice")
        assert app.renderer.lists[-1]["page"] == 2
        await app.execute(f"下一页 {code}", chat="group", user="bob")
        assert app.renderer.lists[-1]["page"] == 2
        app.snapshots.close()
        app.snapshots = SnapshotStore(tmp_path / "state.db")
        await app.execute(f"下一页 {code}", chat="group", user="alice")
        assert app.renderer.lists[-1]["page"] == 3
        await app.execute(f"上一页 {code}", chat="group", user="alice")
        assert app.renderer.lists[-1]["page"] == 2
        await app.execute("下一页", chat="group", user="alice", quote_id="page-one")
        assert app.renderer.lists[-1]["page"] == 2
        assert (
            "没有该查询" in (await app.execute(f"下一页 {code}", chat="other", user="alice")).text
        )
    finally:
        await app.close()
