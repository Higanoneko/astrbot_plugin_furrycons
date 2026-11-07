from dataclasses import dataclass, field
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .commands import CommandRoutes
from .errors import UserError


@dataclass(frozen=True)
class Settings:
    api_base: str = "https://api.furrycons.cn/open"
    api_key: str = field(default="", repr=False)
    timezone: str = "Asia/Shanghai"
    browser_path: str = ""
    browser_download_host: str = ""
    browser_download_proxy: str = ""
    browser_download_timeout: int = 120
    render_timeout: int = 60
    page_size: int = 8
    list_layout: str = "compact"
    interaction_hours: float = 24
    cache_minutes: float = 0
    stale_hours: float = 24
    bare_numbers: bool = False
    bare_names: bool = False
    bare_ids: bool = False
    ai_summary: bool = False
    summary_provider_id: str = ""
    summary_timeout: int = 30
    command_entry: str = "FEC"
    function_aliases: list[str] = field(default_factory=lambda: ["查询展会=查询"])

    @classmethod
    def from_dict(cls, value: dict):
        allowed = cls.__dataclass_fields__
        settings = cls(**{k: v for k, v in value.items() if k in allowed})
        if not isinstance(settings.api_base, str):
            raise UserError("数据源必须是 HTTPS 地址字符串。")
        try:
            url = urlsplit(settings.api_base)
            port = url.port
        except ValueError:
            raise UserError("数据源地址格式无效。") from None
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username is not None
            or url.query
            or url.fragment
            or any(c.isspace() or ord(c) < 32 for c in settings.api_base)
        ):
            raise UserError("数据源必须是无账号、参数或片段的 HTTPS 地址。")
        if not url.hostname or (port is not None and port <= 0):
            raise UserError("数据源地址格式无效。")
        if not isinstance(settings.api_key, str) or any(c in settings.api_key for c in "\r\n"):
            raise UserError("API Key 格式无效，请检查配置。")
        try:
            ZoneInfo(settings.timezone)
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise UserError("时区无效，请使用 Asia/Shanghai 等 IANA 时区名称。") from None
        if type(settings.render_timeout) is not int or not 5 <= settings.render_timeout <= 600:
            raise UserError("渲染超时须为 5–600 秒整数。")
        if not isinstance(settings.browser_path, str):
            raise UserError("浏览器位置须为路径字符串。")
        if any(c in settings.browser_path for c in "\r\n\0"):
            raise UserError("浏览器路径不能包含换行或空字符。")
        for name, schemes in (
            ("browser_download_host", ("https",)),
            ("browser_download_proxy", ("http", "https")),
        ):
            address = getattr(settings, name)
            if not isinstance(address, str):
                raise UserError("浏览器下载源和代理须为地址字符串。")
            if not address:
                continue
            try:
                parsed = urlsplit(address)
                invalid = (
                    parsed.scheme not in schemes
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.query
                    or parsed.fragment
                    or (parsed.port is not None and parsed.port <= 0)
                    or any(c.isspace() or ord(c) < 32 for c in address)
                    or (name == "browser_download_proxy" and parsed.path not in ("", "/"))
                )
            except ValueError:
                invalid = True
            if invalid:
                raise UserError(
                    "下载源须为 HTTPS 地址，代理须为 HTTP(S) 地址；均不含账号、参数或片段。"
                )
        if (
            type(settings.browser_download_timeout) is not int
            or not 5 <= settings.browser_download_timeout <= 600
        ):
            raise UserError("浏览器下载空闲超时须为 5–600 秒整数。")
        if type(settings.page_size) is not int or not 1 <= settings.page_size <= 100:
            raise UserError("每页数量须为 1–100 的整数。")
        if settings.list_layout not in ("compact", "poster", "grid"):
            raise UserError("列表排版须为 compact（紧凑图文）、poster（大图海报）或 grid（双列目录）。")
        if (
            type(settings.interaction_hours) not in (int, float)
            or not 0 < settings.interaction_hours <= 8760
        ):
            raise UserError("交互有效期须为大于 0、不超过 8760 的小时数。")
        for name in ("cache_minutes", "stale_hours"):
            value = getattr(settings, name)
            if type(value) not in (int, float) or not 0 <= value <= 525600:
                raise UserError("缓存与故障回退时限须为非负有限数值。")
        for name in ("bare_numbers", "bare_names", "bare_ids", "ai_summary"):
            if type(getattr(settings, name)) is not bool:
                raise UserError("交互与 AI 总结开关须分别为布尔值。")
        if not isinstance(settings.summary_provider_id, str):
            raise UserError("总结模型须选择 AstrBot 已配置模型的标识。")
        if type(settings.summary_timeout) is not int or not 1 <= settings.summary_timeout <= 120:
            raise UserError("总结超时须为 1–120 秒整数。")
        CommandRoutes(settings)
        return settings
