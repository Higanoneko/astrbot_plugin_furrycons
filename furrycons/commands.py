"""Configured top-level entries bind to stable application actions."""

from .errors import UserError

ACTIONS = {
    "帮助",
    "查询",
    "搜索",
    "详情",
    "选择",
    "翻页",
    "下一页",
    "上一页",
    "单图",
    "状态",
    "原图",
    "原文件",
    "主题列表",
    "主题导入",
    "主题更新",
    "主题默认",
    "主题配色",
    "主题改色",
    "主题副本",
    "主题导出",
}


def command_name(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 32
        or not all(c.isalnum() or c in "_-" for c in value)
    ):
        raise UserError(
            "命令入口须为 1–32 位字母、汉字、数字、下划线或连字符，不含唤醒前缀和空格。"
        )
    return value


class CommandRoutes:
    def __init__(self, settings):
        self.entry = command_name(settings.command_entry)
        self.bindings = {self.entry: ""}
        if not isinstance(settings.function_aliases, list) or len(settings.function_aliases) > 32:
            raise UserError("功能别名须为最多 32 项的列表，每项格式为 快捷入口=功能。")
        for item in settings.function_aliases:
            if not isinstance(item, str) or item.count("=") != 1:
                raise UserError("功能别名格式为 快捷入口=功能，例如 查询展会=查询。")
            alias, action = (part.strip() for part in item.split("=", 1))
            command_name(alias)
            if alias in self.bindings:
                raise UserError(f"命令入口重复或与主入口冲突：{alias}。")
            if action not in ACTIONS:
                raise UserError(f"未知功能 {action}；请绑定现有查询、详情、翻页或主题管理功能。")
            self.bindings[alias] = action

    def validate_occupied(self, occupied):
        conflicts = sorted(set(self.bindings).intersection(occupied))
        if conflicts:
            raise UserError(
                "命令入口已被其他插件占用："
                + "、".join(conflicts)
                + "。请修改本插件入口或别名后重载。"
            )

    def command(self, entry, tail=""):
        return (self.bindings[entry] + " " + tail).strip()

    def parse(self, text):
        parts = text.strip().split(maxsplit=1)
        if not parts or parts[0] not in self.bindings:
            return None
        return self.command(parts[0], parts[1] if len(parts) == 2 else "")
