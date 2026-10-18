# FurryCons 展会图片插件

面向 AstrBot 的 FurryCons 展会图片查询插件。已提供详情、默认/日期/省市查询、名称搜索、稳定分页、持久查询短码、缓存、全部结果单图、可配置裸交互、原文件入口和本地 ZIP 主题导入。可选 AI 总结已接入框架模型接口，真实模型实测仍在进行。**开发中，尚未完成首版全部功能及真实平台验收。**主题商店计划在 P3 落地，Discord 计划在 P2 完成。

## 当前安装与使用

将本目录作为 AstrBot 插件安装，保留 `furrycons/`、`themes/`、`assets/` 和配置文件。已验证 AstrBot 4.29.1、Python 3.14.7；项目代码要求 Python 3.12 及以上，较低框架版本未验收。

在 AstrBot 使用的 Python 环境安装 `requirements.txt`，运行 `python -m playwright install chromium`。Linux 的系统依赖通过 `python -m playwright install-deps chromium` 安装，权限不足时由宿主管理员处理。也可在插件配置填写现有兼容 Chromium 的完整路径。

在 AstrBot 插件配置中填写 FurryCons 工作台提供的 API Key，Authorization 使用原值。数据源默认 `https://api.furrycons.cn/open`，显示时区默认 `Asia/Shanghai`。保存后重载插件。

```text
/FEC 查询
/查询展会
/FEC 查询 广东 30天
/FEC 查询 --single
/FEC 查询 2026-10-18 2026-11-18 显示取消
/FEC 搜索 <名称>
/FEC 翻页 <短码> <页码>
/FEC 单图 <短码>
/FEC 选择 <短码> <序号>
/FEC 详情 <展会ID>
/FEC 原图 <输出码>
/FEC 主题列表
/FEC 主题导入
/FEC 查询 --theme local:my-theme
/FEC 状态
```

默认查询从今天起 365 个自然日，每页 8 场；日期待定记录单独标注，不沿用延期旧日期。名称搜索不限日期，单匹配仍返回候选供选择。查询交互默认有效 24 小时并持久化；同聊天其他用户可用短码，不能跨聊天操作。裸数字、名称和 UUID 触发默认关闭，须由机器人管理者配置；数字开启后仅对应本人在此聊天的最近查询。

完整数据默认缓存 10 分钟，故障时可回退至 24 小时内缓存并显示获取时间和降级信息。详情仍读取最新资料。地区精确名称表带有版本及来源，未知或重名归属不推测。没有出站回执的接入使用短码，实际引用与附件能力需分别验证。

`/` 表示 AstrBot 配置的唤醒前缀。详情标记原文节选和来源；缺失字段保留未明确，未核实状态显示未知。API 故障与不存在的 ID 分别反馈；缺少浏览器时给出部署操作，不切换排版体系。原始图片放在框架插件数据目录，插件不缩放或重编码。

每张图片附带独立输出码，`原图`（或 `原文件`）命令提交生成时的同一文件附件；输出码限所属聊天，默认 24 小时有效，重载不延长有效期。文件丢失或内容变化时提示重新查询。图片发送失败时，在有文件能力的接入尝试同一原文件附件；附件不可用或失败会明确提示。当前已实测本地 AstrBot WebChat 的图片与文件字节一致；Telegram、OneBot 的框架文件通道已接入，实际平台接收仍待验证，其余接入暂不声明附件能力。平台预览、压缩和长图接收限制由平台决定。

AI 总结默认关闭。机器人管理者可在插件配置启用并选择 AstrBot 已配置模型，保存后重载；插件没有独立模型 Key。摘要使用模型从介绍前 12000 字选择有序原文片段，最多 1000 字，不接受模型补写的内容；调用超时、失败、未选模型或输出不可用均回退原文节选。总结与节选都保留来源入口。

主题导入只允许机器人管理员：上传 ZIP 后回复 `/FEC 主题导入`；接入未提供引用文件内容时会提示。默认使用 `builtin:material`，用户可为查询、搜索、详情、选择、翻页或单图追加 `--theme <已安装标识>`，只影响本次输出。安装不改变默认值，同标识不覆盖；导入主题存放在插件数据目录，重载和插件更新保留。主题使用 HTML + Jinja2 与包内静态资源，契约和可运行样包见 [主题作者文档](themes/README.md)。执行 `python scripts/build_theme.py themes/material dist/material-theme.zip` 可打包样例。

## 开发和验收

`uv sync` 后执行 `uv run python -m pytest -q`，可用 `uv run ruff check furrycons main.py tests scripts` 检查代码。

执行 `python scripts/build_plugin.py` 可生成 `dist/astrbot_plugin_furrycons.zip`。安装包仅包含插件代码、配置声明、主题和资源，不含开发凭据、本地运行数据或验收产物。生成目录不进入版本控制。

自动化测试使用可控的外部 API、收发及渲染边界；本地真实浏览器与 AstrBot WebChat 已进行初步验证，QQ、Telegram 等外部平台能力仍待专项验收。

字体来自 [Noto CJK 官方仓库](https://github.com/notofonts/noto-cjk)，按随附 SIL Open Font License 分发。

行政名称表来自 [Administrative-divisions-of-China](https://github.com/modood/Administrative-divisions-of-China)，版本与来源随 `assets/regions.json` 保存，原许可见 `assets/regions.LICENSE`。该表的数据截止 2023-06-30，不宣称覆盖此后所有行政调整；未知或歧义名称不会按地址或相似字推测。
