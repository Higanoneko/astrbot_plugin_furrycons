import asyncio
import struct
from pathlib import Path
from uuid import uuid4

from jinja2 import FileSystemLoader, StrictUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from playwright.async_api import Error, async_playwright

from .errors import RenderError

ROOT = Path(__file__).resolve().parent.parent


class BrowserRenderer:
    def __init__(self, settings, output_dir):
        self.settings = settings
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Semaphore(2)
        self._launch_lock = asyncio.Lock()
        self._engine = None
        self._browser = None

    async def _launch(self):
        async with self._launch_lock:
            return await self._launch_once()

    async def _launch_once(self):
        if self._browser and self._browser.is_connected():
            return self._browser
        if self._engine is None:
            self._engine = await async_playwright().start()
        try:
            self._browser = await self._engine.chromium.launch(
                executable_path=self.settings.browser_path or None,
                headless=True,
            )
            return self._browser
        except Error:
            raise RenderError(
                "浏览器不可用。请机器人管理者在插件环境运行 python -m playwright install chromium，"
                "Linux 还需安装系统依赖（python -m playwright install-deps chromium）；"
                "或配置兼容的浏览器路径后重载。使用 /FEC 状态 检查。"
            ) from None

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._engine:
            await self._engine.stop()
            self._engine = None

    async def status(self):
        try:
            await self._launch()
            return "浏览器已启动；详情渲染使用仓库捆绑的 Noto Sans CJK SC 字体。"
        except RenderError as exc:
            return str(exc)

    async def detail(self, view):
        return await self._render("detail.html", {"event": view})

    async def listing(self, view):
        return await self._render("list.html", {"query": view})

    async def _render(self, template, view):
        env = SandboxedEnvironment(
            loader=FileSystemLoader(ROOT / "themes/material"),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
        )
        html = env.get_template(template).render(**view)
        font = ROOT / "assets/fonts/NotoSansCJKsc-Regular.otf"
        if not font.is_file():
            raise RenderError("插件缺少捆绑中文字体，请重新安装完整插件资源。")
        async with self._lock:
            browser = await self._launch()
            context = await browser.new_context(
                viewport={"width": 1080, "height": 800}, device_scale_factor=1
            )
            try:
                page = await context.new_page()

                # Templates cannot load network or arbitrary local resources.
                async def route(request):
                    if request.request.url == "https://furrycons.invalid/font.otf":
                        await request.fulfill(path=str(font), content_type="font/otf")
                    else:
                        await request.abort()

                await context.route("**/*", route)
                await page.set_content(
                    html, wait_until="load", timeout=self.settings.render_timeout * 1000
                )
                await page.evaluate("document.fonts.ready")
                if not await page.evaluate("document.fonts.check('16px FurryCons')"):
                    raise RenderError("捆绑中文字体加载失败，请检查插件资源。")
                output = self.output_dir / f"{uuid4().hex}.png"
                expected = await page.evaluate(
                    "({width: window.innerWidth, height: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight, window.innerHeight)})"
                )
                await page.screenshot(
                    path=str(output), full_page=True, timeout=self.settings.render_timeout * 1000
                )
                dimensions = struct.unpack(">II", output.read_bytes()[16:24])
                if dimensions != (expected["width"], expected["height"]):
                    output.unlink(missing_ok=True)
                    raise RenderError(
                        "浏览器输出尺寸不完整，未发送截断或缩放图片。请检查运行资源后重试。"
                    )
                return output
            except (Error, OSError, struct.error):
                raise RenderError(
                    "图片渲染失败，请检查浏览器资源、运行内存及渲染超时配置后重试。"
                ) from None
            finally:
                await context.close()
