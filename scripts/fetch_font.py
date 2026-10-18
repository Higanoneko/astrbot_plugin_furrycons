"""Fetch the redistributable font from its official repository (no credentials)."""

from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "assets/fonts"
BASE = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/"
TARGET.mkdir(parents=True, exist_ok=True)
for remote, name in [
    ("OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf", "NotoSansCJKsc-Regular.otf"),
    ("LICENSE", "LICENSE"),
]:
    with urlopen(BASE + remote, timeout=120) as response:
        content = response.read()
    (TARGET / name).write_bytes(content)
    print(f"Saved {name}: {len(content)} bytes")
