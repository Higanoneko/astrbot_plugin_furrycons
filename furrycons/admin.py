"""Management boundary shared by the Dashboard API and configuration validation."""

import asyncio
import hashlib
import json
from dataclasses import asdict

from .commands import ACTIONS, CommandRoutes
from .config import Settings
from .errors import UserError


class AdminSettings:
    def __init__(self, config, active, save, occupied):
        self.config = config
        self.active = active
        self.save = save
        self.occupied = occupied
        self.lock = asyncio.Lock()

    def revision(self):
        raw = json.dumps(dict(self.config), ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    def read(self, *, is_admin=False):
        if not is_admin:
            raise UserError("仅已认证的 Dashboard 管理者可以访问插件配置。")
        configured_settings = Settings.from_dict(dict(self.config))
        values = asdict(configured_settings)
        configured = bool(values.pop("api_key"))
        return {
            "values": values,
            "api_key_configured": configured,
            "revision": self.revision(),
            "requires_reload": configured_settings != self.active,
            "active_entry": self.active.command_entry,
            "actions": sorted(ACTIONS),
        }

    async def update(self, payload, *, is_admin=False):
        if not is_admin:
            raise UserError("仅已认证的 Dashboard 管理者可以修改插件配置。")
        if not isinstance(payload, dict) or set(payload) - {"values", "revision", "clear_api_key"}:
            raise UserError("配置请求格式无效。")
        changes = payload.get("values")
        if not isinstance(changes, dict) or set(changes) - Settings.__dataclass_fields__.keys():
            raise UserError("配置中包含未知项目或格式无效。")
        clear = payload.get("clear_api_key", False)
        if type(clear) is not bool:
            raise UserError("清除 API Key 开关须为布尔值。")
        async with self.lock:
            if payload.get("revision") != self.revision():
                raise UserError("配置已被其他页面修改，请重新读取后再保存。")
            proposed = {**dict(self.config), **changes}
            if changes.get("api_key") == "":
                proposed["api_key"] = self.config.get("api_key", "")
            if clear:
                if changes.get("api_key"):
                    raise UserError("不能同时清除并填写 API Key。")
                proposed["api_key"] = ""
            settings = Settings.from_dict(proposed)
            CommandRoutes(settings).validate_occupied(self.occupied())
            # The host's atomic writer owns the canonical config in data/config;
            # keeping one source lets native and custom configuration coexist.
            previous = dict(self.config)
            try:
                committed = await self.save(asdict(settings))
            except OSError:
                self.config.clear()
                self.config.update(previous)
                raise
            if committed is False:
                raise UserError("配置保存被较新的写入取代，请重新读取后检查。")
            return {
                **self.read(is_admin=True),
                "message": "配置已保存；请回到插件管理页重载插件后生效。",
            }
