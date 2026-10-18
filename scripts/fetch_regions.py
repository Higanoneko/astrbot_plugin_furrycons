"""Bundle a versioned administrative-name lookup, preserving provenance/license."""

import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
REPO = "modood/Administrative-divisions-of-China"
with urlopen(
    Request(
        f"https://api.github.com/repos/{REPO}/commits/master",
        headers={"User-Agent": "FurryCons-development"},
    ),
    timeout=60,
) as response:
    commit = json.load(response)["sha"]
base = f"https://raw.githubusercontent.com/{REPO}/{commit}/"
result = {"source": f"https://github.com/{REPO}", "commit": commit, "data_date": "2023-06-30"}
for name in ("provinces", "cities", "areas"):
    with urlopen(base + f"dist/{name}.json", timeout=60) as response:
        result[name] = json.load(response)
with urlopen(base + "LICENSE", timeout=60) as response:
    license_text = response.read()
(ROOT / "assets/regions.json").write_text(
    json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
)
(ROOT / "assets/regions.LICENSE").write_bytes(license_text)
print(
    json.dumps(
        {
            "commit": commit,
            "provinces": len(result["provinces"]),
            "cities": len(result["cities"]),
            "areas": len(result["areas"]),
        },
        ensure_ascii=False,
    )
)
