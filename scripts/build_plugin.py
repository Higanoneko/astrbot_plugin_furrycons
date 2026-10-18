"""Create a deployable plugin ZIP from an explicit allowlist; never include secrets."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
target = ROOT / "dist/astrbot_plugin_furrycons.zip"
target.parent.mkdir(parents=True, exist_ok=True)
files = [
    ROOT / name
    for name in ("main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt", "README.md")
]
for name in ("furrycons", "themes", "assets", "views", ".astrbot-plugin"):
    folder = ROOT / name
    if folder.exists():
        files.extend(
            p
            for p in folder.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
with ZipFile(target, "w", ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, f"astrbot_plugin_furrycons/{path.relative_to(ROOT).as_posix()}")
with ZipFile(target) as archive:
    names = archive.namelist()
    assert not any(
        "APIKey" in name or ".runtime/" in name or "Reference/" in name for name in names
    )
    assert "astrbot_plugin_furrycons/main.py" in names
print(f"{target} ({target.stat().st_size} bytes; {len(names)} files)")
