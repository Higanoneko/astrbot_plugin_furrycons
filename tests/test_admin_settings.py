import json
from dataclasses import asdict

import pytest

from furrycons.admin import AdminSettings
from furrycons.config import Settings
from furrycons.errors import UserError


def service():
    active = Settings.from_dict({"api_key": "private-fixture"})
    config = asdict(active)

    async def save(values):
        config.update(values)
        return True

    return AdminSettings(config, active, save, lambda: {"help"})


async def test_admin_authorization_and_no_credential_round_trip():
    admin = service()
    with pytest.raises(UserError, match="已认证"):
        admin.read()
    state = admin.read(is_admin=True)
    assert "private-fixture" not in json.dumps(state)
    assert state["api_key_configured"] and not state["requires_reload"]
    with pytest.raises(UserError, match="已认证"):
        await admin.update({"values": {"page_size": 1}, "revision": state["revision"]})
    assert admin.config["page_size"] == 8


async def test_save_keeps_key_and_marks_reload_until_new_instance():
    admin = service()
    result = await admin.update(
        {
            "revision": admin.revision(),
            "values": {
                "page_size": 1,
                "api_key": "",
                "command_entry": "兽展",
                "bare_numbers": True,
            },
        },
        is_admin=True,
    )
    assert result["requires_reload"] and result["active_entry"] == "FEC"
    assert admin.config["api_key"] == "private-fixture"
    assert "private-fixture" not in json.dumps(result)
    restored = AdminSettings(
        admin.config, Settings.from_dict(admin.config), admin.save, admin.occupied
    )
    assert not restored.read(is_admin=True)["requires_reload"]
    assert restored.read(is_admin=True)["active_entry"] == "兽展"
    await admin.update(
        {"revision": admin.revision(), "values": {}, "clear_api_key": True}, is_admin=True
    )
    assert not admin.read(is_admin=True)["api_key_configured"]


@pytest.mark.parametrize(
    "changes",
    [
        {"page_size": 0},
        {"bare_ids": "true"},
        {"timezone": "Invalid/Zone"},
        {"function_aliases": ["FEC=查询"]},
        {"command_entry": "help"},
        {"api_base": None},
        {"api_base": "https://@example.com"},
        {"api_base": "https://example.com:nope"},
        {"api_base": "https://exam ple.com"},
        {"cache_minutes": float("nan")},
        {"interaction_hours": 0},
        {"unknown": 1},
    ],
)
async def test_invalid_settings_never_write(changes):
    admin = service()
    original = dict(admin.config)
    with pytest.raises(UserError):
        await admin.update({"revision": admin.revision(), "values": changes}, is_admin=True)
    assert original == admin.config


async def test_stale_page_cannot_overwrite_a_newer_config():
    admin = service()
    revision = admin.revision()
    await admin.update({"revision": revision, "values": {"page_size": 2}}, is_admin=True)
    with pytest.raises(UserError, match="其他页面"):
        await admin.update({"revision": revision, "values": {"page_size": 3}}, is_admin=True)
    assert admin.config["page_size"] == 2


async def test_write_failure_does_not_leave_in_memory_pending_config():
    admin = service()
    original = dict(admin.config)

    async def failure(values):
        admin.config.update(values)
        raise PermissionError("fixture permission denied")

    admin.save = failure
    with pytest.raises(PermissionError):
        await admin.update(
            {"revision": admin.revision(), "values": {"page_size": 2}}, is_admin=True
        )
    assert admin.config == original
