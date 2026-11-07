import asyncio
import logging
import mimetypes
import re
import struct
from contextlib import suppress
from importlib.metadata import version
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from playwright.async_api import Error, async_playwright

from .errors import RenderError, UserError
from .images import ImageCache
from .preview import preview_data
from .themes import ThemeStore

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


class BrowserRenderer:
    def __init__(self, settings, output_dir, themes=None):
        self.settings = settings
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.images = ImageCache(self.output_dir.parent / "image-cache")
        self._lock = asyncio.Semaphore(2)
        self._launch_lock = asyncio.Lock()
        self._engine = None
        self._browser = None
        self.failure_kind = ""
        self.themes = themes or ThemeStore()
        self._owns_themes = themes is None
        self.themes.command_entry = settings.command_entry

    async def _launch(self):
        async with self._launch_lock:
            return await self._launch_once()

    async def _launch_once(self):
        if self._browser and self._browser.is_connected():
            return self._browser
        try:
            if self._engine is None:
                self._engine = await async_playwright().start()
            self._browser = await self._engine.chromium.launch(
                executable_path=self.settings.browser_path or None,
                headless=True,
            )
            self.failure_kind = ""
            return self._browser
        except Error as error:
            reason = str(error).lower()
            self.failure_kind = (
                "missing_dependencies"
                if any(
                    s in reason
                    for s in ("missing dependencies", "shared libraries", "host system is missing")
                )
                else "permissions"
                if any(s in reason for s in ("permission denied", "eacces"))
                else "missing_browser"
                if any(s in reason for s in ("executable doesn't exist", "doesn't exist at"))
                else "launch_failed"
            )
            raise RenderError(
                "浏览器不可用。请机器人管理者在 FurryCons 快速配置页检测或安装浏览器，"
                "也可在插件环境运行 python -m playwright install chromium，"
                "Linux 还需安装系统依赖（python -m playwright install-deps chromium）；"
                f"或配置兼容的浏览器路径后重载。使用 /{self.settings.command_entry} 状态 检查。"
            ) from None

    async def _discard_browser(self, browser):
        async with self._launch_lock:
            if self._browser is not browser:
                return
            self._browser = None
            try:
                await browser.close()
            except (Error, OSError):
                logger.warning("浏览器资源释放失败，下次渲染将创建新实例。")

    async def close(self):
        try:
            await self.images.close()
        finally:
            try:
                if self._browser:
                    await self._discard_browser(self._browser)
            finally:
                engine, self._engine = self._engine, None
                try:
                    if engine:
                        await engine.stop()
                except (Error, OSError):
                    logger.warning("浏览器运行时释放失败，请检查宿主进程资源。")
                finally:
                    if self._owns_themes:
                        self.themes.close()

    async def status(self):
        result = await self.probe()
        if image := result.pop("image", None):
            image.unlink(missing_ok=True)
        return result["message"]

    async def probe(self):
        result = {
            "ready": False,
            "playwright": version("playwright"),
            "browser_path": self.settings.browser_path or "Playwright 匹配运行时",
        }
        event = preview_data()["event"]
        event.update(
            name="中文字体与浏览器自检",
            introduction="这是部署自检示例，不是真实展会。中文字体：山海相逢。",
            theme_key="builtin:material",
        )
        try:
            image = await self.detail(event)
            result.update(
                ready=True,
                browser=self._browser.version,
                image=image,
                message="浏览器已启动；捆绑 Noto Sans CJK SC 中文字体与真实详情渲染检查通过。",
                failure_kind="",
            )
        except (RenderError, UserError) as error:
            result.update(message=str(error), failure_kind=self.failure_kind or "render_resources")
        except (Error, OSError):
            result.update(
                message="浏览器检测失败，请检查运行环境、执行权限和插件资源。",
                failure_kind="launch_failed",
            )
        return result

    async def detail(self, view):
        return await self._render("detail", {"event": view})

    async def listing(self, view):
        return await self._render("list", {"query": view})

    async def _render(self, template, view):
        theme = self.themes.get(next(iter(view.values())).get("theme_key", "builtin:material"))
        view, media = await self.images.prepare(view, timeout=min(20, self.settings.render_timeout / 2))
        if template == "list":
            view["query"].setdefault("layout", "compact")
        html = theme.render(template, view)
        html = re.sub(
            r"(<head\b[^>]*>)",
            r'\1<base href="https://furrycons.invalid/theme/">',
            html,
            count=1,
            flags=re.IGNORECASE,
        )
        font = ROOT / "assets/fonts/NotoSansCJKsc-Regular.otf"
        if not font.is_file():
            raise RenderError("插件缺少捆绑中文字体，请重新安装完整插件资源。")
        async with self._lock:
            browser = context = output = None
            succeeded = False
            try:
                browser = await self._launch()
                context = await browser.new_context(
                    viewport={"width": 1080, "height": 800},
                    device_scale_factor=1,
                    java_script_enabled=False,
                    service_workers="block",
                )
                page = await context.new_page()

                # Templates cannot load network or arbitrary local resources.
                resource_errors = []

                async def route(request):
                    if request.request.url == "https://furrycons.invalid/font.otf":
                        await request.fulfill(path=str(font), content_type="font/otf")
                    elif request.request.url in media and request.request.resource_type == "image":
                        await request.fulfill(body=media[request.request.url], content_type="image/png")
                    else:
                        url = urlsplit(request.request.url)
                        try:
                            if (
                                url.scheme != "https"
                                or url.netloc != "furrycons.invalid"
                                or not url.path.startswith("/theme/")
                                or url.query
                                or url.fragment
                            ):
                                raise RenderError("主题不允许请求外部资源。")
                            path = theme.resource(unquote(url.path.removeprefix("/theme/")))
                            await request.fulfill(
                                path=str(path),
                                content_type=mimetypes.guess_type(path)[0]
                                or "application/octet-stream",
                            )
                        except (UserError, OSError, ValueError):
                            resource_errors.append(True)
                            await request.abort()

                await context.route("**/*", route)
                await page.set_content(
                    html, wait_until="load", timeout=self.settings.render_timeout * 1000
                )
                await page.evaluate("document.fonts.ready")
                if resource_errors:
                    raise RenderError("主题静态资源缺失或尝试访问外部地址，未生成不完整图片。")
                if await page.evaluate(
                    "Array.from(document.images).some(i => !i.complete || i.naturalWidth === 0)"
                ):
                    raise RenderError("主题图片资源无法解码，未生成不完整图片。")
                if not await page.evaluate(
                    "Array.from(document.fonts).some(f => f.family === 'FurryCons' && f.status === 'loaded')"
                ):
                    raise RenderError("捆绑中文字体加载失败，请检查插件资源。")
                if await page.evaluate("document.documentElement.scrollHeight > 8192"):
                    # Chromium can omit text when compositing backdrop filters
                    # into a very tall full-page bitmap. Themes can retain their
                    # surfaces/edges while disabling only that compositing path.
                    await page.evaluate("document.documentElement.classList.add('long-image')")
                output = self.output_dir / f"{uuid4().hex}.png"
                expected = await page.evaluate(
                    "({width: window.innerWidth, height: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight, window.innerHeight)})"
                )
                await page.screenshot(
                    path=str(output),
                    full_page=True,
                    animations="disabled",
                    timeout=self.settings.render_timeout * 1000,
                )
                dimensions = struct.unpack(">II", output.read_bytes()[16:24])
                if dimensions != (expected["width"], expected["height"]):
                    output.unlink(missing_ok=True)
                    raise RenderError(
                        "浏览器输出尺寸不完整，未发送截断或缩放图片。请检查运行资源后重试。"
                    )
                succeeded = True
                return output
            except (Error, OSError, struct.error) as error:
                if (
                    isinstance(error, Error) and browser is not None
                    and (context is None or not browser.is_connected())
                ):
                    await self._discard_browser(browser)
                raise RenderError(
                    "图片渲染失败，请检查浏览器资源、运行内存及渲染超时配置后重试。"
                ) from None
            finally:
                cleanup_failed = False
                if context is not None:
                    try:
                        await context.close()
                    except asyncio.CancelledError:
                        if output is not None:
                            with suppress(OSError):
                                output.unlink(missing_ok=True)
                        await self._discard_browser(browser)
                        raise
                    except (Error, OSError):
                        cleanup_failed = True
                        await self._discard_browser(browser)
                if output is not None and (not succeeded or cleanup_failed):
                    with suppress(OSError):
                        output.unlink(missing_ok=True)
                if cleanup_failed and succeeded:
                    raise RenderError(
                        "浏览器资源释放失败，未提交本次图片，请稍后重试；持续失败请联系机器人管理者检查运行资源。"
                    ) from None
