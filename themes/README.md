# 主题包契约 v1

主题包提供展示模板与静态资源，不提供 Python、JavaScript、网络请求或插件业务逻辑。列表和详情继续使用相同的查询、来源与浏览器渲染能力。

ZIP 根目录必须含 `theme.json`，禁止额外包一层文件夹。UTF-8 声明如下（完整可运行样例是本目录的 `material/`）：

```json
{
  "format_version": 1,
  "data_version": 1,
  "id": "my-theme",
  "name": "我的主题",
  "version": "1.0.0",
  "author": "主题作者",
  "compatibility": {"plugin_min": "0.1.0", "plugin_max_exclusive": "1.0.0"},
  "templates": {"list": "list.html", "detail": "detail.html"},
  "colors": {
    "primary": "#405f91",
    "mode": "light",
    "advanced": {
      "background": {"label": "背景", "light": "#f4f6fc", "dark": "#141923"},
      "surface": {"label": "卡片", "light": "#ffffff", "dark": "#212938"},
      "text": {"label": "正文", "light": "#1b2436", "dark": "#eef2fa"},
      "muted": {"label": "次要文字", "light": "#576478", "dark": "#b4bfd2"}
    }
  }
}
```

版本和兼容区间均使用三个非负整数的 `MAJOR.MINOR.PATCH`，不含预发布后缀；当前插件版本为 0.1.0。只接受格式版本 1、模板数据版本 1，未知版本明确拒绝。声明字段不可省略，也不接受未知字段；`id` 使用小写字母开头的 1–64 位字母、数字、`_` 或 `-`。主题须声明两类 HTML 模板、主色（六位十六进制）、默认明暗模式 `light`/`dark`，高级颜色项可为空，每项声明标签和两种模式的颜色值。高级颜色项标识使用相同规则。

模板采用 Jinja2 沙箱，自动 HTML 转义，缺失字段报错。支持普通循环、条件、`include`、`extends` 和 `block`，共享文件必须在包内；不支持任意函数调用、宏、导入、运算表达式或修改对象。只开放展示用过滤器：`escape`、`e`、`default`、`length`、`join`、`upper`、`lower`、`replace`、`truncate`、`string`。本契约约束布局模板，筛选、查询、分页与总结在应用层完成。

模板顶层变量：

| 变量 | 数据 |
| --- | --- |
| `event` | 仅详情：`id`、`name`、`status`、`status_label`、`start_label`、`end_label`、`timezone`、`region`、`address`、`introduction`、`introduction_type`、`source`，均为已规范化字符串 |
| `query` | 仅列表：`range`、`code`、`page`、`pages`、`total` 与 `events` 数组；每场包含上述展会字段，以及连续整数 `number` 与 `schedule_note` |
| `theme` | 当前主题 `key`、`id`、`name`、`version`、`mode` 与 `colors` 字典；`colors.primary` 始终存在，高级键与声明一致 |
| `asset('path')` | 返回包内静态资源 URL；路径必须存在且不能越出包目录。CSS 中相对资源以该 CSS 所在目录解析 |

字段未明确时保持应用提供的文字，不在模板中补写举办日期、地点或状态。两类模板必须显示来源，详情显示介绍类型，列表显示范围、短码、页码、总量与每场序号、状态和日期待定提示。保留超长内容自然换行，禁止固定高度裁剪。推荐使用 `@font-face` 的 `FurryCons` 字体及 `https://furrycons.invalid/font.otf`，它由插件离线提供；禁止依赖外部字体或 CDN。

包可含 `.html`、`.css`、`.json`、`.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`、`.otf`、`.ttf`、`.woff`、`.woff2`、`.txt` 和 `.md`。资源路径不能含绝对路径、`..`、反斜线或系统保留文件名，不允许链接、重复名称或仅大小写不同的名称。为避免坏包占满磁盘，ZIP 最多 32 MiB、解压总量最多 64 MiB、最多 256 个文件。浏览器禁止脚本，资源仅限当前主题与捆绑字体；跨域网络与本地文件均不可读取。

修改样例 `id` 和名称后，运行 `python scripts/build_theme.py themes/material dist/my-theme.zip` 打包。上传 ZIP 并回复 `/FEC 主题导入`；只允许机器人管理员操作，导入成功不修改默认主题。同标识已存在时拒绝覆盖，请修改标识以保存新副本。主题存放在 AstrBot 插件数据目录，插件重载/更新保留。

用户可以 `/FEC 主题列表` 查看标识，并为本次输出追加 `--theme local:my-theme`；内置主题标识为 `builtin:material`。查询、搜索、详情、选择、翻页和全部结果单图均适用；本次指定不更改后续默认值。缺少主题或模板不可用时明确报错，不静默切换其他主题。
