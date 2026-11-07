"""Render the layout matrix and same-data before/after images, without sending messages."""

import argparse
import asyncio
import hashlib
import json
import struct
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from math import ceil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from furrycons.config import Settings
from furrycons.preview import preview_data
from furrycons.query import default_query
from furrycons.render import BrowserRenderer
from furrycons.source import FurryConsSource
from furrycons.themes import ThemeStore


def before_theme(themes, output, identity):
    existing = themes.local_dir / f"before-{identity}"
    if existing.is_dir():
        return themes.get(f"local:before-{identity}").key
    prefix = f"themes/{identity}/"
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", prefix], cwd=ROOT, text=True
    ).splitlines()
    archive_path = output / f"before-{identity}.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        for name in names:
            body = subprocess.check_output(["git", "show", f"HEAD:{name}"], cwd=ROOT)
            if name.endswith("theme.json"):
                manifest = json.loads(body)
                manifest.update(id=f"before-{identity}", name=f"升级前 {identity}")
                body = json.dumps(manifest, ensure_ascii=False).encode()
            archive.writestr(name.removeprefix(prefix), body)
    return themes.import_zip(archive_path).key


async def validate(config_path, output):
    output.mkdir(parents=True, exist_ok=True)
    configuration = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path else {}
    settings = Settings.from_dict({**configuration, "render_timeout": 120})
    themes = ThemeStore(output / "state")
    renderer = BrowserRenderer(settings, output / "images", themes)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    fixture = preview_data()
    evidence = {
        "captured_at": now.isoformat(), "live_api": bool(config_path),
        "resource_cache_days": 7, "platform_messages_sent": False, "images": [],
    }
    source = FurryConsSource(settings)
    try:
        if config_path:
            catalog = await source.list_all()
            conventions, label = default_query(catalog, now, settings.timezone)
            if not conventions:
                raise RuntimeError("No live events available for visual validation")
            selected = conventions[:8]
            fixture["query"].update(
                events=[
                    {**event.detail_view(settings.timezone), "number": number,
                     "schedule_note": "日期待定（不归入上述日程范围）"
                     if event.status == "postponed" else ""}
                    for number, event in enumerate(selected, 1)
                ],
                range=label, code="VISUAL", total=len(conventions), pages=ceil(len(conventions) / 8),
            )
            fixture["event"] = (await source.detail(selected[0].id)).detail_view(settings.timezone)
            evidence["source_events"] = [{"id": event.id, "name": event.name} for event in selected]
            evidence["source_image_hosts"] = ["images.furrycons.cn"]
        (output / "input-views.json").write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        async def capture(name, kind, data):
            path = await (renderer.listing(data) if kind == "list" else renderer.detail(data))
            target = output / f"{name}.png"
            path.replace(target)
            blob = target.read_bytes()
            evidence["images"].append({
                "name": name, "kind": kind, "path": str(target.resolve()),
                "dimensions": struct.unpack(">II", blob[16:24]), "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            })
            print(name, evidence["images"][-1]["dimensions"], flush=True)

        for identity in ("material", "liquidglass"):
            previous = before_theme(themes, output, identity)
            before = deepcopy(fixture["query"])
            before["theme_key"] = previous
            await capture(f"before-{identity}", "list", before)
            for mode in ("light", "dark"):
                key = f"builtin:{identity}"
                themes.set_colors(key, {"mode": mode})
                for layout in ("compact", "poster", "grid"):
                    query = deepcopy(fixture["query"])
                    query.update(theme_key=key, layout=layout)
                    await capture(f"{identity}-{mode}-{layout}", "list", query)
                detail = deepcopy(fixture["event"])
                detail["theme_key"] = key
                await capture(f"{identity}-{mode}-detail", "detail", detail)

                edge = preview_data(key)["query"]
                edge.update(code="FIXTURE", range="受控排版检查 · 非真实展会", total=5)
                for number in range(3, 6):
                    event = deepcopy(edge["events"][0])
                    event.update(id=f"fixture-{number}", number=number,
                                 name="长名称排版验证 · " + "山海之间共同相聚" * number)
                    edge["events"].append(event)
                edge["events"][2].update(status="future-state", status_label="未知状态（示例）")
                edge["layout"] = "grid"
                await capture(f"{identity}-{mode}-edge-cases", "list", edge)

            long_query = preview_data(f"builtin:{identity}")["query"]
            seed = long_query["events"][0]
            long_query.update(
                range="全部结果单图 · 80 场受控示例（非真实展会）", code="LONG80", total=80,
                events=[{**seed, "number": i, "id": f"long-{i:03}",
                         "name": f"第 {i} 场 · 中文长名称排版检查"} for i in range(1, 81)],
            )
            for layout in ("compact", "poster", "grid"):
                long_query["layout"] = layout
                await capture(f"{identity}-dark-long-{layout}", "list", long_query)
        evidence["browser"] = renderer._browser.version
        evidence["cached_resource_count"] = len(list(renderer.images.directory.glob("*.png")))
        (output / "result.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Evidence: {output / 'result.json'}", flush=True)
    finally:
        await source.close()
        await renderer.close()
        themes.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(validate(arguments.config, arguments.output))
