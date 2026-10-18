"""Authenticated Dashboard themes, using the shared application boundary."""

import base64
import re
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from astrbot.api.web import PluginUploadFile, error_response, file_response, json_response, request

from .errors import RenderError, UserError


class ThemeWeb:
    def __init__(self, context, app, directory):
        self.app = app
        self.directory = Path(directory) / "imports"
        for path, handler, method in (
            ("themes", self.catalog, "GET"),
            ("themes/action", self.action, "POST"),
            ("themes/preview", self.preview, "POST"),
            ("themes/import", self.import_zip, "POST"),
            ("themes/update/<namespace>/<identity>", self.update_zip, "POST"),
            ("themes/export/<namespace>/<identity>", self.export_zip, "GET"),
        ):
            context.register_web_api(
                "/astrbot_plugin_furrycons/" + path, handler, [method], "FurryCons 主题管理"
            )

    @staticmethod
    def denied():
        return error_response("需要已认证的 Dashboard 管理身份。", status_code=403)

    async def catalog(self):
        if not request.username:
            return self.denied()
        return json_response(
            {
                "themes": await self.app.manage_theme("catalog"),
                "global_default": self.app.themes.preferences.default(),
                "session_defaults": self.app.themes.preferences.sessions(),
                "known_chats": self.app.snapshots.chats(),
                "store_status": "主题商店计划于 P3 提供。",
            }
        )

    async def action(self):
        if not request.username:
            return self.denied()
        try:
            payload = await request.json(default=None)
            fields = {
                "set_colors": {"key", "changes"},
                "reset_colors": {"key"},
                "set_default": {"key", "scope", "chat"},
                "copy": {"key", "identity", "name"},
            }
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("action"), str)
                or payload.get("action") not in fields
            ):
                raise UserError("未知主题管理操作。")
            action = payload.pop("action")
            if set(payload) - fields[action]:
                raise UserError("主题管理请求含未知项目。")
            if action == "set_default" and payload.get("scope") == "session":
                chat = payload.get("chat")
                if not isinstance(chat, str) or not re.fullmatch(
                    r"[^:\s]{1,128}:(?:GroupMessage|FriendMessage|OtherMessage):[^\r\n\x00]{1,512}",
                    chat,
                ):
                    raise UserError("请从已查询会话选择，或填写正确的平台实例:消息类型:会话ID。")
            result = await self.app.manage_theme(action, is_admin=True, **payload)
            if action == "copy":
                result = result.view()
            return json_response(
                {"result": result, "message": "主题设置已保存，后续查询立即生效。"}
            )
        except UserError as error:
            return error_response(str(error), status_code=400)
        except OSError:
            return error_response("主题保存失败，请检查插件数据目录权限和空间。", status_code=500)

    async def preview(self):
        if not request.username:
            return self.denied()
        image = None
        try:
            payload = await request.json(default=None)
            if not isinstance(payload, dict) or set(payload) != {"key", "kind"}:
                raise UserError("预览须指定主题及 list/detail 类型。")
            image = await self.app.manage_theme("preview", is_admin=True, **payload)
            if image.stat().st_size > 8 * 1024 * 1024:
                raise UserError("示例预览超过 8 MiB，请检查主题资源和布局。")
            encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            return json_response(
                {
                    "image": "data:image/png;base64," + encoded,
                    "message": "真实主题模板渲染；活动与内容均为示例。",
                }
            )
        except (UserError, RenderError) as error:
            return error_response(str(error), status_code=400)
        except OSError:
            return error_response("预览文件无法读取，请检查插件数据目录。", status_code=500)
        finally:
            if image:
                with suppress(OSError):
                    image.unlink(missing_ok=True)

    async def _upload(self, key=None):
        if not request.username:
            return self.denied()
        target = None
        try:
            files = await request.files()
            upload = files.get("file")
            if not isinstance(upload, PluginUploadFile) or not (
                upload.filename or ""
            ).lower().endswith(".zip"):
                raise UserError("请选择主题 ZIP 文件。")
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / (uuid4().hex + ".zip")
            await upload.save(target, max_bytes=32 * 1024 * 1024)
            theme = await self.app.manage_theme(
                "update" if key else "import", is_admin=True, path=target, key=key
            )
            return json_response(
                {
                    "theme": theme.view(),
                    "message": "本地主题已更新；独立配色、默认值和旧包备份保留。"
                    if key
                    else "主题已安装，默认值未改变。",
                }
            )
        except UserError as error:
            return error_response(str(error), status_code=400)
        except ValueError:
            return error_response("ZIP 超过 32 MiB 或上传内容无法读取。", status_code=400)
        except OSError:
            return error_response("主题上传失败，请检查数据目录权限与空间。", status_code=500)
        finally:
            if target:
                with suppress(OSError):
                    target.unlink(missing_ok=True)

    async def import_zip(self):
        return await self._upload()

    async def update_zip(self, namespace, identity):
        return await self._upload(namespace + ":" + identity)

    async def export_zip(self, namespace, identity):
        if not request.username:
            return self.denied()
        try:
            path = await self.app.manage_theme(
                "export", is_admin=True, key=namespace + ":" + identity
            )
            return file_response(path, filename=path.name, content_type="application/zip")
        except UserError as error:
            return error_response(str(error), status_code=400)
