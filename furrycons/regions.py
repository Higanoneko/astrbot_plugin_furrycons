"""Exact administrative-name lookup; never infer geography from an address/name."""

import json
from itertools import product
from pathlib import Path

from .errors import UserError

DATA = json.loads(
    (Path(__file__).resolve().parent.parent / "assets/regions.json").read_text(encoding="utf-8")
)
GENERIC = {"市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划"}
ALIASES = {
    "北京": "北京市",
    "京": "北京市",
    "天津": "天津市",
    "津": "天津市",
    "上海": "上海市",
    "沪": "上海市",
    "重庆": "重庆市",
    "渝": "重庆市",
    "冀": "河北省",
    "晋": "山西省",
    "内蒙": "内蒙古自治区",
    "内蒙古": "内蒙古自治区",
    "蒙": "内蒙古自治区",
    "辽": "辽宁省",
    "吉": "吉林省",
    "黑": "黑龙江省",
    "苏": "江苏省",
    "浙": "浙江省",
    "皖": "安徽省",
    "闽": "福建省",
    "赣": "江西省",
    "鲁": "山东省",
    "豫": "河南省",
    "鄂": "湖北省",
    "湘": "湖南省",
    "粤": "广东省",
    "桂": "广西壮族自治区",
    "广西": "广西壮族自治区",
    "琼": "海南省",
    "川": "四川省",
    "蜀": "四川省",
    "贵": "贵州省",
    "黔": "贵州省",
    "云": "云南省",
    "滇": "云南省",
    "藏": "西藏自治区",
    "西藏": "西藏自治区",
    "陕": "陕西省",
    "秦": "陕西省",
    "甘": "甘肃省",
    "陇": "甘肃省",
    "青": "青海省",
    "宁": "宁夏回族自治区",
    "宁夏": "宁夏回族自治区",
    "新": "新疆维吾尔自治区",
    "新疆": "新疆维吾尔自治区",
    "香港": "香港特别行政区",
    "港": "香港特别行政区",
    "澳门": "澳门特别行政区",
    "澳": "澳门特别行政区",
    "台湾": "台湾省",
    "台": "台湾省",
}
LOOKUP = {}


def normalized(value):
    return "".join(value.split()).translate(
        str.maketrans("臺灣縣義蓮東雲連門園", "台湾县义莲东云连门园")
    )


def variants(name):
    result = {name}
    if name.endswith(("省", "市", "县", "区")):
        result.add(name[:-1])
    result.update(k for k, v in ALIASES.items() if v == name)
    return result


def add(path):
    for start in range(len(path)):
        for words in product(*(variants(name) for name in path[start:])):
            LOOKUP.setdefault(normalized("".join(words)), set()).add(path)


provinces = {row["code"]: row["name"] for row in DATA["provinces"]}
cities = {row["code"]: row for row in DATA["cities"]}
for name in list(provinces.values()) + ["香港特别行政区", "澳门特别行政区", "台湾省"]:
    add((name,))
for city in DATA["cities"]:
    if city["name"] not in GENERIC:
        add((provinces[city["provinceCode"]], city["name"]))
for area in DATA["areas"]:
    parent = cities[area["cityCode"]]["name"]
    path = (provinces[area["provinceCode"]],)
    if parent not in GENERIC:
        path += (parent,)
    add(path + (area["name"],))
# City/county names verified against the Taiwan e-government directory;
# same-name cities/counties remain ambiguous unless explicitly qualified.
for name in [
    "基隆市",
    "台北市",
    "新北市",
    "桃园市",
    "新竹市",
    "新竹县",
    "苗栗县",
    "台中市",
    "彰化县",
    "南投县",
    "云林县",
    "嘉义市",
    "嘉义县",
    "台南市",
    "高雄市",
    "屏东县",
    "宜兰县",
    "花莲县",
    "台东县",
    "澎湖县",
]:
    add(("台湾省", name))


def resolve(value):
    matches = LOOKUP.get(normalized(value), set())
    if len(matches) == 1:
        return next(iter(matches))
    return None


def query_region(value):
    matches = LOOKUP.get(normalized(value), set())
    if not matches:
        raise UserError(f"未识别地区“{value}”，请使用省市全称；无法确定的归属不会推测。")
    if len(matches) > 1:
        choices = "、".join("".join(path) for path in sorted(matches)[:6])
        raise UserError(f"地区“{value}”有歧义，请指定全称：{choices}。")
    return next(iter(matches))


def matches_region(raw, target):
    candidates = LOOKUP.get(normalized(raw), set())
    # Multiple exact names may still establish a common province, but never a
    # more specific location that the upstream did not establish.
    return bool(candidates) and all(path[: len(target)] == target for path in candidates)


def display_region(raw):
    if raw == "地区未明确":
        return raw
    return raw if resolve(raw) else f"{raw}（省市归属未明确）"
