"""Non-blocking Playwright deployment with durable progress and render verification."""

import asyncio
import json
import os
import platform
import re
import signal
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import Settings
from .errors import UserError
from .render import BrowserRenderer

RUNNING = {"starting", "installing", "checking"}


def clean_log(text):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)

    def url(match):
        try:
            parsed = urlsplit(match[0])
            host = parsed.netloc.rsplit("@", 1)[-1]
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except ValueError:
            return "[地址已隐藏]"

    return re.sub(r"https?://[^\s<>]+", url, text).strip()[:1000]


def instructions():
    linux = platform.system() == "Linux"
    return {
        "system": platform.system(),
        "python": sys.executable,
        "guidance": (
            "缺少 Linux 共享库时，请宿主管理员在 AstrBot 使用的 Python 环境执行 "
            "python -m playwright install-deps chromium；需要相应系统软件源与安装权限。"
            if linux
            else "请使用 AstrBot 的 Python 环境；检查浏览器目录写入/执行权限及系统兼容性。"
        )
        + " 制品镜像和下载代理不能替代 Linux 系统依赖的软件源。"
        + " 现有浏览器路径以实际渲染结果为准，不保证任意 Chrome 版本兼容。",
    }


class BrowserDeployment:
    def __init__(self, directory, renderer, saved_settings):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "browser_job.json"
        self.renderer = renderer
        self.saved_settings = saved_settings
        self.task = None
        self.process = None
        self.last_image = None
        self.lock = asyncio.Lock()
        self.probe_lock = asyncio.Lock()
        self.job = {"state": "idle", "logs": [], "message": "尚未发起安装。"}
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(stored, dict) and isinstance(stored.get("logs"), list):
                self.job = stored
                if self.job.get("state") in RUNNING:
                    self.job.update(
                        state="interrupted",
                        message="上次安装被宿主重启或插件重载中断；请重新检测后主动安装。",
                    )
                    self._persist()
        except (OSError, ValueError):
            pass

    def _persist(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def _update(self, **values):
        self.job.update(values, updated=datetime.now(UTC).isoformat())
        self._persist()

    def _log(self, value):
        value = clean_log(value)
        if value:
            self.job["logs"] = [*self.job["logs"], value][-200:]
            self._update()

    def status(self, *, is_admin=False):
        self._authorize(is_admin)
        return {
            "job": json.loads(json.dumps(self.job)),
            "environment": instructions(),
            "preview_available": bool(self.last_image and self.last_image.is_file()),
        }

    @staticmethod
    def _authorize(is_admin):
        if not is_admin:
            raise UserError("仅已认证的管理者可以检测或部署浏览器。")

    async def probe(self, *, is_admin=False):
        self._authorize(is_admin)
        async with self.probe_lock:
            result = await self.renderer.probe()
            image = result.pop("image", None)
            if self.last_image:
                self.last_image.unlink(missing_ok=True)
                self.last_image = None
            if image:
                self.last_image = image
            result["preview_available"] = bool(image)
            return {"probe": result, **self.status(is_admin=True)}

    async def start(self, *, is_admin=False):
        self._authorize(is_admin)
        async with self.lock:
            if self.task and not self.task.done():
                raise UserError("已有浏览器安装正在进行，请查看当前进度。")
            settings = Settings.from_dict(dict(self.saved_settings()))
            self.job = {
                "state": "starting",
                "logs": [],
                "message": "准备安装匹配的 Chromium；下载后还需渲染自检。",
                "started": datetime.now(UTC).isoformat(),
            }
            try:
                self._persist()
            except OSError:
                self.job.update(
                    state="failed", message="无法创建安装任务，请检查插件数据目录写入权限。"
                )
                raise
            self.task = asyncio.create_task(self._install(settings))
            return self.status(is_admin=True)

    async def _install(self, settings):
        try:
            env = os.environ.copy()
            env["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
            env["PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT"] = str(
                settings.browser_download_timeout * 1000
            )
            if settings.browser_download_host:
                env["PLAYWRIGHT_DOWNLOAD_HOST"] = settings.browser_download_host.rstrip("/")
                env.pop("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST", None)
            if settings.browser_download_proxy:
                for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                    env[name] = settings.browser_download_proxy
            self._update(
                state="installing",
                message="正在安装当前 Playwright 版本所需的 Chromium；进度见日志。",
            )
            options = (
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "playwright",
                "install",
                "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                **options,
            )
            async with asyncio.timeout(1800):
                pending = ""
                while chunk := await self.process.stdout.read(4096):
                    pending += chunk.decode("utf-8", errors="replace")
                    pieces = re.split(r"[\r\n]", pending)
                    pending = pieces.pop()
                    for line in pieces:
                        self._log(line)
                    if len(pending) > 4000:
                        self._log(pending)
                        pending = ""
                self._log(pending)
                code = await self.process.wait()
            if code:
                self._update(
                    state="failed",
                    message=f"浏览器安装程序退出码 {code}；请检查下载源、代理、写入权限及日志。",
                    exit_code=code,
                )
                return
            self._update(
                state="checking", message="下载步骤完成，正在实际渲染中文自检图。", exit_code=0
            )
            # Always verify the newly installed matched runtime, even when the
            # administrator has selected another existing executable for chat.
            matched = BrowserRenderer(replace(settings, browser_path=""), self.directory / "checks")
            try:
                result = await matched.probe()
                image = result.pop("image", None)
                if image:
                    image.unlink(missing_ok=True)
            finally:
                await matched.close()
            if not result["ready"]:
                self._update(
                    state="failed",
                    message="安装步骤完成但匹配运行时未通过渲染检测。" + result["message"],
                    matched_probe=result,
                )
                return
            active = (await self.probe(is_admin=True))["probe"]
            state = "succeeded" if active["ready"] else "needs_configuration"
            self._update(
                state=state,
                message=(
                    "匹配运行时安装和中文渲染通过，当前聊天渲染也可用。"
                    if active["ready"]
                    else "匹配运行时安装成功；当前配置的浏览器仍不可用，请清除或修正浏览器路径并重载。"
                ),
                matched_probe=result,
                active_probe=active,
            )
        except asyncio.CancelledError:
            await self._stop_process()
            self._update(
                state="interrupted", message="安装因插件关闭或重载中断，请重新检测后再安装。"
            )
            raise
        except TimeoutError:
            await self._stop_process()
            self._update(
                state="failed",
                message="安装超过 30 分钟，已停止；请检查下载源、代理与磁盘空间后重试。",
            )
        except (OSError, UserError):
            await self._stop_process()
            try:
                self._update(
                    state="failed",
                    message="无法启动或保存安装任务，请检查插件数据目录写入权限、Python 环境及磁盘空间。",
                )
            except OSError:
                self.job.update(
                    state="failed", message="任务状态无法写入，请检查插件数据目录权限和磁盘空间。"
                )
        except Exception as error:  # noqa: BLE001 - background task boundary
            await self._stop_process()
            self.job.update(
                state="failed", message="部署检测异常，未确认可用；请重新检测或检查宿主环境。"
            )
            try:
                self._log(f"部署任务异常：{type(error).__name__}")
            except OSError:
                pass
        finally:
            self.process = None

    async def _stop_process(self):
        if self.process is None or self.process.returncode is not None:
            return
        try:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(self.process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await killer.wait()
            else:
                os.killpg(self.process.pid, signal.SIGKILL)
            await self.process.wait()
        except ProcessLookupError:
            pass

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.last_image:
            self.last_image.unlink(missing_ok=True)
