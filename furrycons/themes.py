"""Versioned display-only theme packages, shared by chat and management views."""

import json
import re
import stat
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import quote, unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from jinja2 import FileSystemLoader, StrictUndefined, TemplateError, nodes, select_autoescape
from jinja2.sandbox import ImmutableSandboxedEnvironment

from .errors import UserError

BUILTINS = Path(__file__).resolve().parent.parent / "themes"
PLUGIN_VERSION = (0, 1, 0)
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
ALLOWED_SUFFIXES = {
    ".html",
    ".css",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".otf",
    ".ttf",
    ".woff",
    ".woff2",
    ".txt",
    ".md",
}
FILTERS = {
    "escape",
    "e",
    "default",
    "length",
    "join",
    "upper",
    "lower",
    "replace",
    "truncate",
    "string",
}


def package_path(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise UserError("主题资源路径无效，须使用包内相对路径。")
    path = PurePosixPath(name)
    if path.is_absolute() or name != path.as_posix() or len(name) > 240:
        raise UserError("主题资源路径无效，禁止绝对路径或重复分隔符。")
    for part in path.parts:
        if (
            part in (".", "..")
            or ":" in part
            or part.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>"|?*' for c in part)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        ):
            raise UserError("主题资源路径包含越界路径或系统保留名称。")
    return path


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){2}", value):
        raise UserError("主题版本须为 MAJOR.MINOR.PATCH 三个非负整数。")
    return tuple(map(int, value.split(".")))


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise UserError(f"主题 {label} 字段不符契约，请参阅主题作者文档。")


def _manifest(value):
    _object(
        value,
        (
            "format_version",
            "data_version",
            "id",
            "name",
            "version",
            "author",
            "compatibility",
            "templates",
            "colors",
        ),
        "声明",
    )
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        raise UserError("不支持此主题包格式版本；当前支持 format_version=1。")
    if type(value["data_version"]) is not int or value["data_version"] != 1:
        raise UserError("不支持此主题模板数据版本；当前支持 data_version=1。")
    if not isinstance(value["id"], str) or not IDENTIFIER.fullmatch(value["id"]):
        raise UserError("主题标识须为小写字母开头的 1–64 位字母、数字、下划线或连字符。")
    for field in ("name", "author"):
        if not isinstance(value[field], str) or not value[field].strip() or len(value[field]) > 100:
            raise UserError("主题名称与作者须为 1–100 字的非空文本。")
    _version(value["version"])
    compatibility = value["compatibility"]
    _object(compatibility, ("plugin_min", "plugin_max_exclusive"), "兼容声明")
    lower, upper = (
        _version(compatibility["plugin_min"]),
        _version(compatibility["plugin_max_exclusive"]),
    )
    if not lower <= PLUGIN_VERSION < upper:
        raise UserError("主题兼容区间不包含当前插件 0.1.0，未安装。")
    _object(value["templates"], ("list", "detail"), "模板")
    for name in value["templates"].values():
        if package_path(name).suffix != ".html":
            raise UserError("两类主题模板均须为 HTML 文件。")
    colors = value["colors"]
    _object(colors, ("primary", "mode", "advanced"), "配色")
    if not isinstance(colors["primary"], str) or not COLOR.fullmatch(colors["primary"]):
        raise UserError("主题主色须为六位十六进制颜色。")
    if colors["mode"] not in ("light", "dark") or not isinstance(colors["advanced"], dict):
        raise UserError("主题须声明 light/dark 模式与高级颜色项。")
    if len(colors["advanced"]) > 32:
        raise UserError("主题高级颜色项最多 32 项。")
    for name, item in colors["advanced"].items():
        if not IDENTIFIER.fullmatch(name) or name == "primary":
            raise UserError("主题高级颜色标识无效或与主色冲突。")
        _object(item, ("label", "light", "dark"), "高级颜色项")
        if not isinstance(item["label"], str) or not 1 <= len(item["label"].strip()) <= 50:
            raise UserError("高级颜色项须提供简短显示名称。")
        if any(
            not isinstance(item[mode], str) or not COLOR.fullmatch(item[mode])
            for mode in ("light", "dark")
        ):
            raise UserError("高级颜色项须提供两种模式的六位十六进制颜色。")
    return value


def _json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise UserError("主题声明含重复字段。")
        result[name] = value
    return result


@dataclass(frozen=True)
class Theme:
    key: str
    path: Path
    manifest: dict

    def resource(self, name):
        relative = package_path(name)
        result = (self.path / relative).resolve()
        if not result.is_relative_to(self.path.resolve()) or not result.is_file():
            raise UserError("主题静态资源缺失或超出包目录。")
        return result

    def asset(self, name):
        self.resource(name)
        return "https://furrycons.invalid/theme/" + quote(name, safe="/")

    def view(self):
        colors = self.manifest["colors"]
        return {
            "key": self.key,
            "id": self.manifest["id"],
            "name": self.manifest["name"],
            "version": self.manifest["version"],
            "mode": colors["mode"],
            "colors": {
                "primary": colors["primary"],
                **{k: v[colors["mode"]] for k, v in colors["advanced"].items()},
            },
        }

    def environment(self):
        env = ImmutableSandboxedEnvironment(
            loader=FileSystemLoader(self.path),
            autoescape=select_autoescape(default=True),
            undefined=StrictUndefined,
        )
        env.globals.clear()
        env.globals["asset"] = self.asset
        env.filters = {name: value for name, value in env.filters.items() if name in FILTERS}
        return env

    def render(self, kind, data):
        try:
            return (
                self.environment()
                .get_template(self.manifest["templates"][kind])
                .render(**data, theme=self.view())
            )
        except TemplateError:
            raise UserError(f"主题 {self.key} 的 {kind} 模板不符合数据契约，未生成图片。") from None


def load_theme(path, namespace):
    path = Path(path)
    try:
        manifest_path = path / "theme.json"
        if not manifest_path.is_file() or manifest_path.stat().st_size > 65536:
            raise UserError("主题须在 ZIP 根目录提供不超过 64 KiB 的 theme.json。")
        value = _manifest(
            json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
        )
        theme = Theme(f"{namespace}:{value['id']}", path, value)
        env = theme.environment()
        for file in path.rglob("*"):
            if file.is_symlink():
                raise UserError("主题不允许符号链接。")
            if not file.is_file():
                continue
            package_path(file.relative_to(path).as_posix())
            if file.suffix.lower() not in ALLOWED_SUFFIXES:
                raise UserError("主题包含非展示资源文件，不能安装业务代码或脚本。")
            if file.suffix.lower() == ".css":
                validate_css(theme, file.read_text(encoding="utf-8"), file.parent.relative_to(path))
            if file.suffix.lower() != ".html":
                continue
            parsed = env.parse(file.read_text(encoding="utf-8"))
            if any(
                parsed.find_all(
                    (
                        nodes.Macro,
                        nodes.Import,
                        nodes.FromImport,
                        nodes.CallBlock,
                        nodes.Add,
                        nodes.Sub,
                        nodes.Mul,
                        nodes.Div,
                        nodes.FloorDiv,
                        nodes.Pow,
                        nodes.Mod,
                        nodes.Concat,
                    )
                )
            ):
                raise UserError("主题模板只允许展示表达式，不支持宏、导入或运算表达式。")
            for call in parsed.find_all(nodes.Call):
                if not (
                    isinstance(call.node, nodes.Name)
                    and call.node.name == "asset"
                    and len(call.args) == 1
                    and isinstance(call.args[0], nodes.Const)
                    and not call.kwargs
                    and call.dyn_args is None
                    and call.dyn_kwargs is None
                ):
                    raise UserError("主题模板只能调用 asset('包内路径')。")
                theme.resource(call.args[0].value)
            for node in parsed.find_all((nodes.Include, nodes.Extends)):
                if not isinstance(node.template, nodes.Const) or not isinstance(
                    node.template.value, str
                ):
                    raise UserError("共享模板必须使用固定的包内 HTML 路径。")
                if theme.resource(node.template.value).suffix != ".html":
                    raise UserError("共享模板须为包内 HTML 文件。")
        for template in value["templates"].values():
            theme.resource(template)
        validate_examples(theme)
        return theme
    except (OSError, UnicodeError, json.JSONDecodeError, TemplateError, RecursionError):
        raise UserError("主题声明、模板语法或资源无法读取，请检查 UTF-8 文件与模板契约。") from None


class _VisibleContent(HTMLParser):
    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.hidden = 0
        self.parts = []
        self.styles = []
        self.tags = set()
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag in ("script", "iframe", "object", "embed", "form", "base"):
            raise UserError("主题只提供静态展示，不能含脚本、嵌入页面或表单。")
        for name, value in attrs:
            if name.startswith("on") or (value and value.strip().lower().startswith("javascript:")):
                raise UserError("主题不允许脚本事件或 JavaScript 链接。")
            if name == "style" and value:
                validate_css(self.theme, value)
            if name == "srcset":
                raise UserError("主题资源须使用明确的 src 路径，不支持 srcset。")
            if value and (name == "src" or (tag == "link" and name == "href")):
                validate_resource(self.theme, value)
        if tag == "style":
            self.in_style = True
        if tag in ("style", "script", "head"):
            self.hidden += 1
        if tag == "a":
            self.parts.extend(value for key, value in attrs if key == "href" and value)

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False
        if tag in ("style", "script", "head"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if self.in_style:
            self.styles.append(data)
        if not self.hidden:
            self.parts.append(data)


def validate_resource(theme, value, relative=None):
    relative = PurePosixPath(".") if relative is None else PurePosixPath(relative)
    if value == "https://furrycons.invalid/font.otf":
        return
    url = urlsplit(value)
    if url.scheme or url.netloc:
        if (
            url.scheme != "https"
            or url.netloc != "furrycons.invalid"
            or not url.path.startswith("/theme/")
        ):
            raise UserError("主题资源须在包内，不能依赖外部地址或本地文件。")
        name = unquote(url.path.removeprefix("/theme/"))
    else:
        name = (relative / unquote(url.path)).as_posix()
    if url.query or url.fragment:
        raise UserError("主题资源路径不支持参数或片段。")
    theme.resource(name)


def validate_css(theme, css, relative=None):
    for value in re.findall(r"url\(\s*([^)]*?)\s*\)", css, flags=re.IGNORECASE):
        validate_resource(theme, value.strip().strip("\"'"), relative)
    for value in re.findall(r"@import\s+[\"']([^\"']+)[\"']", css, flags=re.IGNORECASE):
        validate_resource(theme, value, relative)


def contract_examples():
    event = {
        name: f"示例{name}字段"
        for name in (
            "id",
            "name",
            "status",
            "status_label",
            "start",
            "end",
            "start_label",
            "end_label",
            "timezone",
            "region",
            "address",
            "description",
            "introduction",
            "introduction_type",
            "source",
        )
    }
    query = {
        "range": "示例查询范围",
        "code": "ABC123",
        "page": 1,
        "pages": 2,
        "total": 9,
        "events": [
            {**event, "number": 1, "schedule_note": ""},
            {
                **event,
                "id": "延期示例id",
                "name": "延期示例名称",
                "number": 2,
                "schedule_note": "示例日期待定",
            },
        ],
    }
    return {"detail": {"event": event}, "list": {"query": query}}


def validate_examples(theme):
    examples = contract_examples()
    required = {
        "detail": [
            examples["detail"]["event"][k]
            for k in (
                "id",
                "name",
                "status_label",
                "start_label",
                "end_label",
                "timezone",
                "region",
                "address",
                "introduction",
                "introduction_type",
                "source",
            )
        ],
        "list": [
            "示例查询范围",
            "ABC123",
            "示例name字段",
            "示例status_label字段",
            "示例start_label字段",
            "示例end_label字段",
            "示例region字段",
            "示例日期待定",
            "延期示例名称",
            "https://www.furrycons.cn",
        ],
    }
    for kind, context in examples.items():
        content = _VisibleContent(theme)
        content.feed(theme.render(kind, context))
        if not {"html", "head", "body"} <= content.tags:
            raise UserError("两类主题模板均须输出完整 html、head、body 文档。")
        for css in content.styles:
            validate_css(theme, css)
        rendered = "".join(content.parts)
        if any(value not in rendered for value in required[kind]):
            raise UserError(f"主题 {kind} 模板缺少必需信息或来源，请使用完整样包的数据契约。")


class ThemeStore:
    def __init__(self, data_dir=None):
        self.local_dir = Path(data_dir) / "themes" if data_dir else None
        if self.local_dir:
            self.local_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key="builtin:material"):
        if not isinstance(key, str) or key.count(":") != 1:
            raise UserError("主题标识须为 builtin:名称 或 local:名称；可用 /FEC 主题列表 查看。")
        namespace, identity = key.split(":")
        if not IDENTIFIER.fullmatch(identity):
            raise UserError("主题标识无效，请查看已安装主题列表。")
        root = (
            BUILTINS if namespace == "builtin" else self.local_dir if namespace == "local" else None
        )
        if root is None or not (root / identity / "theme.json").is_file():
            raise UserError("该主题尚未安装或已不可用，请使用 /FEC 主题列表 查看。")
        theme = load_theme(root / identity, namespace)
        if theme.key != key:
            raise UserError("主题目录与声明标识不一致，请重新导入正确主题包。")
        return theme

    def installed(self):
        result = []
        for namespace, root in (("builtin", BUILTINS), ("local", self.local_dir)):
            if root:
                for path in sorted(root.iterdir()):
                    if (
                        path.is_dir()
                        and not path.name.startswith(".")
                        and (path / "theme.json").is_file()
                    ):
                        key = f"{namespace}:{path.name}"
                        try:
                            theme = self.get(key)
                            result.append(
                                f"{key} · {theme.manifest['name']} · {theme.manifest['version']}"
                            )
                        except UserError:
                            result.append(f"{key} · 不可用，请机器人管理者检查主题包")
        return result

    def import_zip(self, archive_path):
        if self.local_dir is None:
            raise UserError("未配置框架插件数据目录，无法安装主题。")
        try:
            archive_path = Path(archive_path)
            if archive_path.stat().st_size > 32 * 1024 * 1024:
                raise UserError("主题 ZIP 超过 32 MiB，请减少包内资源。")
            with (
                ZipFile(archive_path) as archive,
                TemporaryDirectory(prefix=".import-", dir=self.local_dir) as temporary,
            ):
                files = archive.infolist()
                if (
                    len(files) > 512
                    or sum(not f.is_dir() for f in files) > 256
                    or sum(f.file_size for f in files) > 64 * 1024 * 1024
                ):
                    raise UserError("主题包超过文件数或 64 MiB 解压资源上限。")
                names = set()
                for item in files:
                    name = item.filename.rstrip("/") if item.is_dir() else item.filename
                    relative = package_path(name)
                    if (
                        name.casefold() in names
                        or stat.S_ISLNK(item.external_attr >> 16)
                        or item.flag_bits & 1
                    ):
                        raise UserError("主题 ZIP 含重名、链接或加密文件，不支持导入。")
                    names.add(name.casefold())
                    if not item.is_dir():
                        if relative.suffix.lower() not in ALLOWED_SUFFIXES:
                            raise UserError("主题包含非展示资源文件，不能安装业务代码或脚本。")
                        target = Path(temporary) / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(item))
                theme = load_theme(temporary, "local")
                destination = self.local_dir / theme.manifest["id"]
                if destination.exists():
                    raise UserError("该本地主题标识已存在，未覆盖；请修改包内标识以保存新副本。")
                Path(temporary).rename(destination)
                return self.get(theme.key)
        except (BadZipFile, OSError, RuntimeError, ValueError):
            raise UserError(
                "主题 ZIP 无法读取或安装，原有主题保持可用，请检查文件后重试。"
            ) from None
