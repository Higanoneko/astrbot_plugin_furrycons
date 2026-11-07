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

版本和兼容区间均使用三个非负整数的 `MAJOR.MINOR.PATCH`，不含预发布后缀；当前插件版本为 0.2.12。只接受格式版本 1、模板数据版本 1，未知版本明确拒绝。声明字段不可省略，也不接受未知字段；`id` 使用小写字母开头的 1–64 位字母、数字、`_` 或 `-`。主题须声明两类 HTML 模板、主色（六位十六进制）、默认明暗模式 `light`/`dark`，高级颜色项可为空，每项声明标签和两种模式的颜色值。高级颜色项标识使用相同规则。

模板采用 Jinja2 沙箱，自动 HTML 转义，缺失字段报错。支持普通循环、条件、`include`、`extends` 和 `block`，共享文件必须在包内；不支持任意函数调用、宏、导入、运算表达式或修改对象。只开放展示用过滤器：`escape`、`e`、`default`、`length`、`join`、`upper`、`lower`、`replace`、`truncate`、`string`。本契约约束布局模板，筛选、查询、分页与总结在应用层完成。

模板顶层变量：

| 变量 | 数据 |
| --- | --- |
| `event` | 仅详情：`id`、`name`、`status`、`status_label`、`start_label`、`end_label`、`timezone`、`region`、`address`、`introduction`、`introduction_type`、`source`，均为已规范化字符串 |
| `query` | 仅列表：`range`、`code`、`page`、`pages`、`total`、`command_entry`（配置的主入口名称，无唤醒前缀）与 `events` 数组；每场包含上述展会字段，以及连续整数 `number` 与 `schedule_note` |
| `theme` | 当前主题 `key`、`id`、`name`、`version`、`mode` 与 `colors` 字典；`colors.primary` 始终存在，高级键与声明一致 |
| `asset('path')` | 返回包内静态资源 URL；路径必须存在且不能越出包目录。CSS 中相对资源以该 CSS 所在目录解析 |

字段未明确时保持应用提供的文字，不在模板中补写举办日期、地点或状态。两类模板必须显示来源，详情显示介绍类型，列表显示范围、短码、页码、总量与每场序号、状态和日期待定提示。保留超长内容自然换行，禁止固定高度裁剪。推荐使用 `@font-face` 的 `FurryCons` 字体及 `https://furrycons.invalid/font.otf`，它由插件离线提供；禁止依赖外部字体或 CDN。

包可含 `.html`、`.css`、`.json`、`.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`、`.otf`、`.ttf`、`.woff`、`.woff2`、`.txt` 和 `.md`。资源路径不能含绝对路径、`..`、反斜线或系统保留文件名，不允许链接、重复名称或仅大小写不同的名称。为避免坏包占满磁盘，ZIP 最多 32 MiB、解压总量最多 64 MiB、最多 256 个文件。浏览器禁止脚本，资源仅限当前主题、捆绑字体与本次渲染提供的官方图片资源；模板不能自行请求外部网络或读取本地文件。

修改样例 `id` 和名称后，运行 `python scripts/build_theme.py themes/material dist/my-theme.zip` 打包。上传 ZIP 并回复 `/FEC 主题导入`；只允许机器人管理员操作，导入成功不修改默认主题。同标识已存在时拒绝覆盖，请修改标识以保存新副本。主题存放在 AstrBot 插件数据目录，插件重载/更新保留。

用户可以 `/FEC 主题列表` 查看标识，并为本次输出追加 `--theme local:my-theme`；内置主题标识为 `builtin:material`。查询、搜索、详情、选择、翻页和全部结果单图均适用；本次指定不更改后续默认值。缺少主题或模板不可用时明确报错，不静默切换其他主题。

另外提供 `builtin:liquidglass`，共享同一数据契约与捆绑字体。配色与主题包独立保存，渲染时的 `theme.colors` 已包含管理员覆盖值，`theme.mode` 为生效的明暗模式；模板应引用这些字段，避免把声明为可配置的颜色写死。`theme.origin` 为本地副本来源标识，无来源时是 `None`。

管理员可设置全局/会话默认值，优先级是单次指定、会话、全局、初始 Material。默认值、配色和副本来源保存在框架插件数据目录，不写回内置主题文件。`主题副本 <标识> <新标识>` 将布局与当前配色克隆到独立本地主题；`主题导出 <标识>` 导出布局资源 ZIP 以供编辑，ZIP 不携带独立配色记录。编辑后修改包内标识再导入，已有本地主题不被覆盖。所有修改与导出操作均只允许机器人管理员。

## 0.2.0 的图文数据与预设排版

格式版本和数据版本仍为 1，旧主题不必修改。`theme.json` 可选声明 `"layouts": ["compact", "poster", "grid"]`（无重复，可声明子集）；未声明时按主题自身布局渲染，显式要求未声明的排版会提示不支持。使用这些新字段或声明的主题应把 `compatibility.plugin_min` 设为 `0.2.0`。

- `query.layout`：本次生效的 `compact`、`poster` 或 `grid`，主题负责提供所声明排版的完整布局。
- 列表中的每场与详情的 `event.poster`、`event.organizer_logo`：`null`，或 `{url, width, height}`。实际渲染中 `url` 是插件提供的受控资源地址，宽高是已解码图片的像素尺寸。应用层原始视图在获取图片前可能以 0 表示尺寸未知。
- `event.organization_name`：主办方名称，缺失时为空字符串。
- `event.poster_note`：渲染时提供的缺图说明。列表、详情共用 `_poster.html`，可参考两套内置主题。

使用 `<img src="{{ event.poster.url }}">` 展示，并在访问前检查对象不为 `null`；不要把资源 URL 拼接为磁盘路径，不要自行生成外部 URL。应用会从官方图片接口取得资源并缓存复用 7 天，主题包和导出 ZIP 不包含这些展会资源。图片获取失败不应隐藏展会文字；可用 `object-fit: contain` 保留全图，或 `cover` 按比例铺满；不要拉伸图像。背景装饰也可复用相同的 `<img>` 引用。

CSS 可引用文档内的固定 SVG 滤镜（例如 `url('#fc-capsule-refraction')`）；SVG 内的图片必须使用 `asset('包内图片')`。脚本、外链 SVG 资源与任意联网仍禁止。Liquid Glass 胶囊用 currentColor 生成跟随文字强调色的淡染色；胶囊统一采用 20% 颜色覆盖；卡片内不叠加面蒙层或重复磨砂，只对既有背景做 SVG 位移，文字保持不透明。详情标题旁独立胶囊使用同色的轻玻璃底；时间字段的日期待定说明为普通文字。详情背景复用对应海报，大信息卡保持轻薄细边。超长图关闭 backdrop-filter，保留淡染色、细边、高光和清晰文字。

当页面高度超过 8192 px，渲染器会为根元素添加 `long-image` 类。内置 Liquid Glass 在此模式禁用 `backdrop-filter`，保留渐变、边缘、高光和阴影，避免 Chromium 超长截图中尾部和中部文字漏绘。主题作者可使用 `html.long-image` 提供同类静态回退；不要缩放、裁剪或隐藏列表内容来规避长图。

0.2.1 的两套内置主题在列表与详情中统一采用同一信息卡片内左文右图；`compact` 使用标准右图，`poster` 使用较大右图，`grid` 在双列卡片内使用右侧缩略图。文字区与图片均不另设独立卡片表面。模板数据契约与布局标识兼容 0.2.0。

0.2.2 将两套内置主题的右图改为满高覆盖卡片，列表三种预设和详情共用图片层。图片左侧遮罩保留 10% 不透明度，并将模糊图层渐变叠加到清晰图层上；文字独立显示。图片按比例覆盖右侧区域，超出的画面由卡片圆角裁切。无图时保留右侧文字说明。此版只修订内置排版，数据契约不变。

0.2.3 将海报层扩展至卡片全宽，位于信息文字下方。图片左侧从卡片外缘起保留 10% 不透明度，经连续遮罩过渡至右侧清晰区域；避免在卡片内部以带有不透明度的图片边缘硬切入。三种预设分别调整渐变位置，缺图时仍使用右侧说明，数据契约不变。

0.2.4 的图片引用新增可选 `edge_colors: {top, middle, bottom}`，为图片可见左边缘三段的六位十六进制颜色；全透明图使用 `transparent`。读取已有资源缓存时同样派生配色，不延长有效期或触发重新下载。引用此字段的主题应声明 `plugin_min: 0.2.4`。旧主题与旧快照继续兼容。

内置主题将海报设为 `height:100%; width:auto; max-width:none` 并右对齐。背景使用边缘配色的纵向渐变，模糊层同时处理填色与原比例图片，过渡位置依据实际图片宽度调整；浏览器仍禁用页面脚本。图片外缘由卡片裁切。此安排覆盖此前按卡片宽度执行 cover 的实现。
