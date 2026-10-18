"""AstrBot's authenticated plugin Web API adapter."""

from astrbot.api.web import error_response, file_response, json_response, request

from .errors import UserError


class AdminWeb:
    def __init__(self, context, settings, schema, deployment):
        self.settings = settings
        self.schema = schema
        self.deployment = deployment
        context.register_web_api(
            "/astrbot_plugin_furrycons/settings", self.get_settings, ["GET"], "FurryCons 快速配置"
        )
        context.register_web_api(
            "/astrbot_plugin_furrycons/settings",
            self.save_settings,
            ["POST"],
            "保存 FurryCons 配置",
        )
        for path, handler, method in (
            ("browser", self.browser_status, "GET"),
            ("browser/probe", self.browser_probe, "POST"),
            ("browser/install", self.browser_install, "POST"),
            ("browser/preview", self.browser_preview, "GET"),
        ):
            context.register_web_api(
                "/astrbot_plugin_furrycons/" + path, handler, [method], "FurryCons 浏览器部署"
            )

    async def browser_status(self):
        if not request.username:
            return error_response("需要已认证的 Dashboard 管理身份。", status_code=403)
        return json_response(self.deployment.status(is_admin=True))

    async def browser_probe(self):
        if not request.username:
            return error_response("需要已认证的 Dashboard 管理身份。", status_code=403)
        return json_response(await self.deployment.probe(is_admin=True))

    async def browser_install(self):
        if not request.username:
            return error_response("需要已认证的 Dashboard 管理身份。", status_code=403)
        try:
            return json_response(await self.deployment.start(is_admin=True))
        except UserError as error:
            return error_response(str(error), status_code=400)
        except OSError:
            return error_response("无法创建部署任务，请检查插件数据目录权限。", status_code=500)

    async def browser_preview(self):
        if not request.username:
            return error_response("需要已认证的 Dashboard 管理身份。", status_code=403)
        path = self.deployment.last_image
        if not path or not path.is_file():
            return error_response("当前没有成功的自检图，请先检测浏览器。", status_code=404)
        return file_response(path, filename="furrycons-browser-check.png", content_type="image/png")

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
