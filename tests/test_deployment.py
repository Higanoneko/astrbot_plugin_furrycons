"""Deployment decisions; real browser/download evidence is recorded separately."""

import asyncio
from dataclasses import asdict

import pytest

from furrycons import deployment
from furrycons.config import Settings
from furrycons.errors import UserError


class Renderer:
    ready = True

    def __init__(self, *_):
        pass

    async def probe(self):
        return {"ready": self.ready, "message": "真实边界替身", "playwright": "fixture"}

    async def close(self):
        pass


class Process:
    def __init__(self, gate, code=0):
        self.gate = gate
        self.stdout = self
        self.returncode = code
        self.read_once = False

    async def read(self, _):
        await self.gate.wait()
        if self.read_once:
            return b""
        self.read_once = True
        return b"download 50%\rdownload 100%\n"

    async def wait(self):
        return self.returncode


async def fixture_job(tmp_path, monkeypatch, code=0):
    gate = asyncio.Event()
    captured = {}

    async def spawn(*args, **kwargs):
        captured.update(command=args, env=kwargs["env"])
        return Process(gate, code)

    monkeypatch.setattr(deployment.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(deployment, "BrowserRenderer", Renderer)
    values = asdict(
        Settings.from_dict(
            {
                "browser_download_host": "https://example.com/browser",
                "browser_download_proxy": "http://127.0.0.1:7890",
                "browser_download_timeout": 45,
            }
        )
    )
    manager = deployment.BrowserDeployment(tmp_path, Renderer(), lambda: values)
    return manager, gate, captured


async def test_install_requires_management_identity(tmp_path):
    manager = deployment.BrowserDeployment(tmp_path, Renderer(), dict)
    for call in (manager.probe, manager.start):
        with pytest.raises(UserError, match="管理者"):
            await call()
    with pytest.raises(UserError):
        manager.status()
    assert not manager.path.exists()


async def test_background_progress_duplicate_guard_environment_and_completion(
    tmp_path, monkeypatch
):
    manager, gate, captured = await fixture_job(tmp_path, monkeypatch)
    start = await manager.start(is_admin=True)
    assert start["job"]["state"] == "starting"
    await asyncio.sleep(0)
    assert manager.status(is_admin=True)["job"]["state"] == "installing"
    with pytest.raises(UserError, match="正在进行"):
        await manager.start(is_admin=True)
    assert captured["command"][-3:] == ("playwright", "install", "chromium")
    assert captured["env"]["PLAYWRIGHT_DOWNLOAD_HOST"] == "https://example.com/browser"
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert captured["env"]["PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT"] == "45000"
    gate.set()
    await manager.task
    result = manager.status(is_admin=True)
    assert result["job"]["state"] == "succeeded"
    assert "download 100%" in result["job"]["logs"]
    await manager.close()
    restored = deployment.BrowserDeployment(tmp_path, Renderer(), dict)
    assert restored.status(is_admin=True)["job"]["state"] == "succeeded"


async def test_successful_installer_exit_is_not_success_without_render_check(tmp_path, monkeypatch):
    manager, gate, _ = await fixture_job(tmp_path, monkeypatch)
    monkeypatch.setattr(Renderer, "ready", False)
    await manager.start(is_admin=True)
    gate.set()
    await manager.task
    assert manager.status(is_admin=True)["job"]["state"] == "failed"
    assert not manager.job["matched_probe"]["ready"]


async def test_failed_download_keeps_failure_after_reload(tmp_path, monkeypatch):
    manager, gate, _ = await fixture_job(tmp_path, monkeypatch, code=1)
    await manager.start(is_admin=True)
    gate.set()
    await manager.task
    await manager.close()
    restored = deployment.BrowserDeployment(tmp_path, Renderer(), dict)
    assert restored.job["state"] == "failed" and restored.job["exit_code"] == 1


async def test_reload_cancels_active_job_and_persists_interruption(tmp_path, monkeypatch):
    manager, _, _ = await fixture_job(tmp_path, monkeypatch)
    await manager.start(is_admin=True)
    await asyncio.sleep(0)
    await manager.close()
    assert manager.task.done()
    restored = deployment.BrowserDeployment(tmp_path, Renderer(), dict)
    assert restored.job["state"] == "interrupted"


def test_progress_log_never_retains_proxy_credentials_or_url_query():
    text = deployment.clean_log(
        "\x1b[31mFailed https://person:private@proxy.test/path?token=secret#data\x1b[0m"
    )
    assert text == "Failed https://proxy.test/path"


@pytest.mark.parametrize(
    "values",
    [
        {"browser_download_host": "http://example.com"},
        {"browser_download_host": "https://example.com/?key=secret"},
        {"browser_download_proxy": "http://user:pass@localhost:7890"},
        {"browser_download_proxy": "http://localhost:7890/path"},
        {"browser_download_proxy": "http://localhost:bad"},
        {"browser_download_host": 123},
        {"browser_download_timeout": 0},
        {"browser_download_timeout": True},
        {"browser_path": "path\ncommand"},
    ],
)
def test_deployment_configuration_rejects_unsafe_or_invalid_values(values):
    with pytest.raises(UserError):
        Settings.from_dict(values)
