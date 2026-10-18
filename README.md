# FurryCons 展会图片插件

面向 AstrBot 的 FurryCons 展会图片查询插件。已提供详情、默认/日期/省市查询、名称搜索、稳定分页、持久查询短码、缓存、全部结果单图、可配置裸交互、原文件入口、两套内置主题、本地 ZIP 导入更新、会话主题、独立改色、可选 AI 总结及 WebUI 管理。**开发中，尚未完成首版全部验收。**列表配图与三种可切换排版、Discord 计划在 P2 完成，主题商店计划在 P3 落地。QQ/Telegram 真实收发验收按维护者安排暂缓。

## 当前安装与使用

将本目录作为 AstrBot 插件安装，保留 `furrycons/`、`themes/`、`assets/` 和配置文件。最低支持 AstrBot 4.29.1，已验证该版本和 Python 3.14.7；项目代码要求 Python 3.12 及以上。

在 AstrBot 使用的 Python 环境安装 `requirements.txt`，运行 `python -m playwright install chromium`。Linux 的系统依赖通过 `python -m playwright install-deps chromium` 安装，权限不足时由宿主管理员处理。也可在插件配置填写现有兼容 Chromium 的完整路径。

在 AstrBot 插件配置中填写 FurryCons 工作台提供的 API Key，Authorization 使用原值。数据源默认 `https://api.furrycons.cn/open`，显示时区默认 `Asia/Shanghai`。保存后重载插件。

也可从插件卡片的详情打开“FurryCons 快速配置”可视化视图，在 Dashboard 内配置数据源、时区、分页与缓存、入口及功能别名、裸触发和总结开关。页面通过框架 bridge 调用受认证保护的插件 API，不读取父页面登录信息。API Key 只显示是否已配置；留空保存会保留原值，勾选清除才删除。无效值或入口冲突不会保存，陈旧页面需重新读取配置。保存结果会标明待重载状态；回到插件管理页重载，再重新读取即可确认当前生效入口。

配置沿用 AstrBot 的 `data/config/astrbot_plugin_furrycons_config.json`，由框架原子写入，与原生插件配置共用一份数据；插件目录更新不会覆盖它。Dashboard 登录身份或有框架 `plugin` 管理权限的 API 凭据可管理此接口，聊天管理操作独立检查机器人管理员身份。

快速配置页提供浏览器检测、中文自检图下载和主动安装。下载源 `browser_download_host` 可留空使用 Playwright 官方源；代理 `browser_download_proxy` 与连接超时只用于安装，先保存再点击“安装匹配 Chromium”。任务后台执行，日志与状态保存在插件数据目录，重开页面可查看；插件重载会中断正在进行的安装，之后需重新检测并主动发起。只有实际中文图片渲染通过才显示可用。已有浏览器路径需保存并重载后检测，下载匹配运行时不会自动覆盖自定义路径。Linux 缺少共享库或安装权限时按页面指引由宿主管理员处理，下载镜像不能替代系统依赖源。

同页“主题管理”可浏览已安装主题、用真实模板预览示例列表和详情、导入/更新/导出 ZIP、另存布局副本、设置主色及明暗和声明的高级颜色，以及全局或指定会话默认值。这些主题操作立即用于后续查询，无需重载；预览资料明确标为示例。更新仅针对明确选择的本地主题且 ZIP 标识必须一致；先校验后替换，保留独立配色、默认值和插件数据目录中的 `.previous-` 旧包备份。商店、在线安装与在线更新仍属 P3。

```text
/FEC 查询
/查询展会
/FEC 查询 广东 30天
/FEC 查询 --single
/FEC 查询 2026-10-18 2026-11-18 显示取消
/FEC 搜索 <名称>
/FEC 翻页 <短码> <页码>
/FEC 下一页 <短码>
/FEC 上一页 <短码>
/FEC 单图 <短码>
/FEC 选择 <短码> <序号>
/FEC 详情 <展会ID>
/FEC 原图 <输出码>
/FEC 主题列表
/FEC 主题导入
/FEC 主题更新 local:my-theme
/FEC 查询 --theme local:my-theme
/FEC 查询 --theme builtin:liquidglass
/FEC 主题默认
/FEC 主题默认 全局 builtin:liquidglass
/FEC 主题默认 本会话 builtin:material
/FEC 主题默认 本会话 清除
/FEC 主题配色 builtin:material
/FEC 主题改色 builtin:liquidglass 主色 #bca7ff
/FEC 主题改色 builtin:liquidglass 明暗 dark
/FEC 主题改色 builtin:liquidglass background #172133
/FEC 主题改色 builtin:liquidglass 重置
/FEC 主题副本 builtin:liquidglass my-glass
/FEC 主题导出 local:my-glass
/FEC 状态
```

默认查询从今天起 365 个自然日，每页 8 场；日期待定记录单独标注，不沿用延期旧日期。名称搜索不限日期，单匹配仍返回候选供选择。查询交互默认有效 24 小时并持久化；同聊天其他用户可用短码，不能跨聊天操作。裸数字、名称和 UUID 触发默认关闭，须由机器人管理者配置；数字开启后仅对应本人在此聊天的最近查询。

完整数据默认缓存 10 分钟，故障时可回退至 24 小时内缓存并显示获取时间和降级信息。详情仍读取最新资料。地区精确名称表带有版本及来源，未知或重名归属不推测。没有出站回执的接入使用短码，实际引用与附件能力需分别验证。

`/` 表示 AstrBot 配置的唤醒前缀。详情标记原文节选和来源；缺失字段保留未明确，未核实状态显示未知。API 故障与不存在的 ID 分别反馈；缺少浏览器时给出部署操作，不切换排版体系。原始图片放在框架插件数据目录，插件不缩放或重编码。

主入口可在插件配置的 `command_entry` 修改；`function_aliases` 每行一项 `快捷入口=功能`，例如 `查展=查询`、`展会详情=详情`、`设置主题=主题默认`。别名直接绑定功能，主入口改名后仍然有效，与完整命令共享参数及机器人管理员权限。入口只填名称，不填 `/` 等唤醒前缀；保存后重载插件生效，旧入口会被清理。示例以默认 FEC 展示，实际提示使用配置后的入口；框架使用 `!` 前缀时输入 `!FEC 查询`。请在本插件配置中修改路由，避免同时在框架命令管理中重命名这些入口。

重复入口、未知功能或与其他已启用插件的命令冲突会明确拒绝加载，请修正配置后重载；后加载插件造成的冲突也会在使用时报告。`下一页`/`上一页` 使用当前用户在当前聊天中该短码最近生成的页，未浏览过从第一页起算；记录跨重载保留。正确引用列表图时仍以被引用图片的页码为准。

每张图片附带独立输出码，`原图`（或 `原文件`）命令提交生成时的同一文件附件；输出码限所属聊天，默认 24 小时有效，重载不延长有效期。文件丢失或内容变化时提示重新查询。图片发送失败时，在有文件能力的接入尝试同一原文件附件；附件不可用或失败会明确提示。当前已实测本地 AstrBot WebChat 的图片与文件字节一致；Telegram、OneBot 的框架文件通道已接入，实际平台接收仍待验证，其余接入暂不声明附件能力。平台预览、压缩和长图接收限制由平台决定。

AI 总结默认关闭。机器人管理者可在插件配置启用并选择 AstrBot 已配置模型，保存后重载；插件没有独立模型 Key。摘要使用模型从介绍前 12000 字选择有序原文片段，最多 1000 字，不接受模型补写的内容；调用超时、失败、未选模型或输出不可用均回退原文节选。总结与节选都保留来源入口。

主题导入只允许机器人管理员：上传 ZIP 后回复 `/FEC 主题导入`；接入未提供引用文件内容时会提示。更新时回复 `/FEC 主题更新 local:标识`，包内标识须与目标一致。默认使用 `builtin:material`，用户可为查询、搜索、详情、选择、翻页或单图追加 `--theme <已安装标识>`，只影响本次输出。安装不改变默认值，普通导入不会覆盖已有标识；导入主题存放在插件数据目录，重载和插件更新保留。主题使用 HTML + Jinja2 与包内静态资源，契约和可运行样包见 [主题作者文档](themes/README.md)。执行 `python scripts/build_theme.py themes/material dist/material-theme.zip` 可打包样例。

内置 `builtin:material`（初始默认）与 `builtin:liquidglass`。主题优先级为单次指定、本会话默认、全局默认、初始 Material；会话按平台实例和群/频道/私聊身份隔离。只有机器人管理员能设置/清除默认值、改色、保存副本或导出主题，群主/频道管理员身份不自动取得权限。默认值不可用时明确报错，可单次指定可用主题或清除对应默认值，不静默换主题。

主色和 `light`/`dark` 模式独立保存，高级颜色只能修改主题声明的项，`主题配色` 可查看。配色按主题保存，对使用该主题的会话生效；与包版本独立，插件更新不覆盖用户状态。`主题副本` 克隆布局和当前配色为 `local:` 主题，后续源主题配色变化不影响副本。布局、字号和间距通过导出 ZIP 编辑并以新标识导入，不提供简化布局控件。导出 ZIP 包含声明、布局与资源，独立配色仍保存在宿主数据目录；导入/另存副本不切换默认主题。

## 开发和验收

升级前可备份 `data/config/astrbot_plugin_furrycons_config.json` 和 `data/plugin_data/astrbot_plugin_furrycons/`。使用 AstrBot 插件页更新代码；用户主题、配色、默认值、缓存、快照和图片存放在后者中，请保留该数据目录。已在 AstrBot 4.29.1 的实际 ZIP 替换安装中验证这些数据及有效原文件引用保留，过期短码不会因升级延长有效期。更新或重载会中断正在进行的浏览器安装，请完成后重新检测。

| 接入 | 当前证据与限制 |
| --- | --- |
| AstrBot WebChat | 已验证查询、翻页、选择、图片、原文件字节一致及插件更新后短码恢复。 |
| NapCat / OneBot | 已接入 AstrBot 通用消息与 aiocqhttp 文件路径；真实群聊/私聊收发、引用、附件和长图接收待维护者验收。 |
| QQ 官方 | 已接入通用图片与短码命令；真实适用会话、引用回执及长图接收待验收，暂不声明附件能力。 |
| Telegram | 已接入通用消息及文件路径；真实群聊/频道/私聊、引用、附件和长图接收待维护者验收。 |
| Discord | P2。 |

两套主题的浅深色长列表和长介绍已在 Chromium 实际渲染并查看；本地生成成功不代表外部聊天平台能接收相同长度。主题 WebUI 的真实接口上传更新已验证，页面文件选择上传尚待专项核对；Linux 缺共享库分支及完整冷下载也未全部验收，不据此宣称所有部署环境可用。

`uv sync` 后执行 `uv run python -m pytest -q`，可用 `uv run ruff check furrycons main.py tests scripts` 检查代码。

执行 `python scripts/build_plugin.py` 可生成 `dist/astrbot_plugin_furrycons.zip`。安装包仅包含插件代码、配置声明、主题和资源，不含开发凭据、本地运行数据或验收产物。生成目录不进入版本控制。

自动化测试使用可控的外部 API、收发及渲染边界；本地真实浏览器与 AstrBot WebChat 已进行初步验证，QQ、Telegram 等外部平台能力仍待专项验收。

字体来自 [Noto CJK 官方仓库](https://github.com/notofonts/noto-cjk)，按随附 SIL Open Font License 分发。

行政名称表来自 [Administrative-divisions-of-China](https://github.com/modood/Administrative-divisions-of-China)，版本与来源随 `assets/regions.json` 保存，原许可见 `assets/regions.LICENSE`。该表的数据截止 2023-06-30，不宣称覆盖此后所有行政调整；未知或歧义名称不会按地址或相似字推测。
