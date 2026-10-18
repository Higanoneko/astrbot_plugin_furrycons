"""AstrBot's authenticated plugin Web API adapter."""

from astrbot.api.web import error_response, json_response, request

from .errors import UserError


class AdminWeb:
    def __init__(self, context, settings, schema):
        self.settings = settings
        self.schema = schema
        context.register_web_api(
            "/astrbot_plugin_furrycons/settings", self.get_settings, ["GET"], "FurryCons 快速配置"
        )
        context.register_web_api(
            "/astrbot_plugin_furrycons/settings",
            self.save_settings,
            ["POST"],
            "保存 FurryCons 配置",
        )

    async def get_settings(self):
        if not request.username:
            return error_response("需要已认证的 Dashboard 管理身份。", status_code=403)
        try:
            return json_response({**self.settings.read(is_admin=True), "schema": self.schema})
        except UserError as error:
            return error_response(str(error), status_code=400)

    async def save_settings(self):
        if not request.username:
            return error_response("需要已认证的 Dashboard 管理身份。", status_code=403)
        try:
            payload = await request.json(default=None)
            return json_response(await self.settings.update(payload, is_admin=True))
        except UserError as error:
            return error_response(str(error), status_code=400)
        except OSError:
            return error_response("配置写入失败，请检查宿主目录权限后重试。", status_code=500)
