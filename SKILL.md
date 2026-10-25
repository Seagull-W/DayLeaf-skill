---
name: DayLeaf
description: 通过本地 JSON 管理个人日程，支持自然语言添加、查询、修改、删除以及今日、本周聊天摘要；为现有 Hermes 渠道提供每日摘要入口。
metadata:
  hermes:
    tags: [agenda, calendar, schedule, 日程]
    category: productivity
    requires_toolsets: [terminal]
---

# DayLeaf

用户要求安排会议、记录约会、查看行程、修改或取消安排时调用。本 Skill 面向单个用户的个人日程；同一 Hermes profile 的微信、QQ 等渠道共享数据。不要在陌生人的会话或群聊中自动公开个人日程。不是定时提醒引擎，不支持循环事件、账户或日历同步。

## 调用方式

Python 3.9+，Linux/macOS（使用标准库 `fcntl` 文件锁）；需要系统 IANA 时区数据，无需 pip 包。
脚本位于本 SKILL.md 同目录下 `scripts/agenda.py`，默认安装路径为：
`~/.hermes/skills/productivity/DayLeaf/scripts/agenda.py`。
迁移时按本 Skill 的实际目录定位。默认数据文件为 `${HERMES_HOME:-~/.hermes}/agenda/events.json`；可用 `--data /绝对路径/events.json` 覆盖。多 profile 调用应显式传入对应 profile 的数据路径；每日发送进程须使用相同 profile 的 `HERMES_HOME`。

每次从 stdin 读取一个 JSON 对象，stdout 输出一个 JSON 对象。成功 `ok:true`、退出码 0；校验、冲突、读写或发送失败 `ok:false`、退出码 1。CLI 参数用法错误由 argparse 输出到 stderr、退出码 2。不要用 shell 字符串拼接用户内容；通过 stdin 或正确引用的 JSON 文件传递。

```bash
python3 ~/.hermes/skills/productivity/DayLeaf/scripts/agenda.py <<'JSON'
{"action":"add","title":"项目讨论","start":"2026-10-26T09:00:00+08:00","end":"2026-10-26T10:00:00+08:00","notes":"带上草稿"}
JSON
```

接口示例（每行代表一次独立调用）：

```json
{"action":"list"}
{"action":"list","query":"项目"}
{"action":"list","period":"today","timezone":"Asia/Shanghai"}
{"action":"list","period":"week"}
{"action":"list","from":"2026-10-26T00:00:00+08:00","to":"2026-10-27T00:00:00+08:00"}
{"action":"update","id":"查询返回的完整ID","changes":{"start":"2026-10-26T11:00:00+08:00","end":"2026-10-26T12:00:00+08:00"}}
{"action":"update","id":"查询返回的完整ID","changes":{"notes":null,"end":null}}
{"action":"delete","id":"查询返回的完整ID"}
{"action":"summary","period":"today"}
{"action":"summary","period":"week","timezone":"Asia/Shanghai"}
```

`list` 返回 `events`、`timezone` 和当前 `now`；`summary` 额外返回可直接回复的 `text`。查询按时间排序，区间为左闭右开，包含跨天持续中的事件。本周为当地周一 00:00 到下周一 00:00。摘要中的跨天事件显示原始起止日期。添加返回生成的唯一 `id`，修改返回完整事件，删除返回被删除的事件。

## 对话规则

- 自然语言由 Agent 理解，脚本不猜测时间。先用 `list` 返回的 `now` 确定当前日期。默认 Asia/Shanghai，并在首次使用或跨时区安排时说明；用户指定其他时区时，每次调用传入 IANA 名称。
- “明天下午三点”依据用户时区转为具体日期、15:00 和对应 UTC 偏移；“晚点”“周五左右”“三点”若不能确定日期或上午/下午，先询问。“本周五”已过去而用户语境指未来时也需澄清，不能悄悄挪到下周。
- 输入时间必须含 UTC 偏移，例如 `+08:00` 或 `Z`。`timezone` 用于查询日期范围和摘要展示，不会替输入时间补偏移。跨 DST 时根据 IANA 时区确认偏移；重复出现或不存在的本地时间先澄清，不能猜测。结束时间未知时省略；不要默认一小时。
- 修改、删除先查询候选。仅在用户意图唯一匹配时使用完整 ID；出现多项或目标不明确，展示标题、日期、时间供用户选择，然后再调用。不能取第一条、按模糊标题批量删除或用 ID 前缀。即便查询只有一项，也不能忽略用户描述与该项不符的情况。
- 只修改用户指定字段。若平移事件，应确定是否保留原时长，并同时传入新的开始和结束时间，避免旧结束时间残留。
- 用户随任务给出操作细节时，按「任务附带信息（notes）」规则保存。
- 添加/修改遇到 `error:conflict` 时尚未保存。告知冲突的标题和时间，询问改时间还是允许重叠；只有用户明确接受重叠后，才重试相同请求并加 `allow_conflict:true`。脚本在锁内重新检查冲突。
- 有结束时间的事件按 `[start,end)` 检测冲突，相邻事件不冲突；无结束时间视为一个时间点，只检查同一时间点或落入其他事件区间，不假设持续时长。
- 普通对话直接通过当前 Hermes 会话回复；成功后说清具体日期、时间及操作结果。查询今日/本周优先使用 `summary.text`，不要再调用发送工具导致重复消息。失败时不得宣称已保存。
- 文件损坏时脚本拒绝继续写入；保留原文件，由用户从备份恢复，不要清空重建。写入使用同目录临时文件、fsync、原子替换以及稳定的 sidecar 锁，适用于本地文件系统；不要手工并发编辑 JSON。

## 任务附带信息（notes）

- 用户在安排任务的同时给出该任务本身的操作细节（步骤、参数、公式、参考链接、目标、验收标准、复现要点等）时，把这份信息结构化写入该事件的 `notes`；用换行分条，必要时加序号。
- 用户只给标题/时间、没有附带细节时，省略 `notes`，不写空字符串、不写“无”，不要自行补全或推测。
- 原文中的数字、标识符、公式、链接逐字保留，不改写、不归纳成“见上文”；整段 notes 超过 2000 字符时保留关键条目并说明已截断。
- 用户显式要求给已有任务写入、追加、替换或清空附带信息时，先用 `list` 定位唯一事件，再用 `update` 的 `changes.notes`；追加时把旧 notes 一并拼入新值，清空用 `notes:null`；匹配不唯一时按现有规则让用户选择，不得猜。
- notes 只出现在文本摘要的 📝 行，卡片不显示；不要把细节塞进 `title`。
- 不要把敏感凭证（密码、token、卡号）写入 notes。

规则背景与示例见 [docs/task-notes.md](docs/task-notes.md)。

## 每日摘要推送

入口 `scripts/daily_summary.py` 默认先发纯文本摘要，再将 HTML/CSS 渲染为 PNG 并发送；底层直接调用现有 `hermes send --to TARGET --file - --json`，无 LLM、无新消息服务。Hermes 的 `hermes_cli/send_cmd.py` 提供该入口：该命令复用 `send_message_tool` 和现有平台适配器；微信、QQ 等插件平台可能需要正在运行的 gateway 及有效平台会话。发送限制与可用性由现有平台决定。

先预览（不发送）：

```bash
python3 ~/.hermes/skills/productivity/DayLeaf/scripts/daily_summary.py --dry-run
hermes send --list --json
```

在用户指定推送渠道后，从上述目录或当前可信会话上下文取得准确目标，不猜测聊天 ID。实际推送：

```bash
python3 ~/.hermes/skills/productivity/DayLeaf/scripts/daily_summary.py --to 'weixin:实际chat_id' --hermes ~/.local/bin/hermes
```

`weixin` 只是格式示例，使用本机目录列出的实际平台名和目标。用 `command -v hermes` 确认可执行文件路径。支持 `--data`、`--timezone`；结果包含 `sent`。空日程仍发送“暂无安排”文本与空白日程卡。发送超时（90 秒）、返回失败或 skipped 均退出非零，不自动重试，避免不确定投递状态造成重复。

以下为其他部署环境可用的 **cron 模板**，替换 TARGET 和 Hermes 路径。cron 与 systemd timer 选择一种，不要重复安装。08:00 依据 cron 守护进程的时区，必须与期望时区核对，`--timezone` 只控制摘要日期，不改变 cron 运行时间。

```cron
0 8 * * * HERMES_HOME=~/.hermes /usr/bin/python3 ~/.hermes/skills/productivity/DayLeaf/scripts/daily_summary.py --timezone Asia/Shanghai --hermes ~/.local/bin/hermes --to 'TARGET' >> ~/.hermes/agenda-daily.log 2>&1
```

也可用 systemd oneshot service 的 `ExecStart` 调用同一命令，并用 timer 的 `OnCalendar=*-*-* 08:00:00 Asia/Shanghai` 调度。service 设置相应 `Environment=HERMES_HOME=...`。选择一种调度方式，每日只配置一个任务；入口本身不做去重，手动重复执行会重复发送。投递不确定时先检查 Hermes 日志再决定重试。长摘要由现有 Hermes 渠道处理，受平台长度限制。

## 测试

```bash
python3 -m unittest discover -s ~/.hermes/skills/productivity/DayLeaf/tests -v
```

测试仅使用临时目录与模拟发送，不触碰真实日程或向聊天渠道发送消息。


## 可视化卡片

复用已安装 Chrome/Chromium 的 headless 模式与系统中文字体，不安装浏览器框架或 Python 依赖。模板为 `templates/card.html`，渲染器为 `scripts/render_card.py`。PNG 为 720×1200 竖屏，米白/鼠尾草绿浅色主题与墨绿深色主题；日期、星期、日程总数、开始/结束时间按视觉层次展示。无结束时间明确标“结束未定”。标题自动折行，按浏览器实际高度分页；超长单项跨页延续，保持文字字号，不截断。卡片不放备注，备注保留在文本摘要中。

```bash
# 本地预览；仅生成，不推送
python3 ~/.hermes/skills/productivity/DayLeaf/scripts/daily_summary.py --dry-run --theme light
python3 ~/.hermes/skills/productivity/DayLeaf/scripts/daily_summary.py --dry-run --theme dark
# 无浏览器时可仅使用文本入口
python3 ~/.hermes/skills/productivity/DayLeaf/scripts/daily_summary.py --dry-run --text-only
```

`--chrome` 指定浏览器路径，`--output-dir` 指定产物目录。默认保存在数据文件旁的 `cards/YYYY-MM-DD/`；同日同主题重新渲染会覆盖同名文件，发送仅使用本次返回的图片列表。目录保留供排错，可按需删除旧日期目录。`previews/` 中是示例数据生成的两套主题样张，不是真实日程。

所有日程标题通过 JSON 数据传给模板、以 textContent 渲染，无外部资源、无脚本插入。保留 Chrome 沙箱；若受限执行环境不支持启动浏览器，应在正常宿主环境运行，不添加 `--no-sandbox`。

文本和图片使用同一次日程快照。先独立发送文本，成功后再渲染和依次发送 `MEDIA:/绝对路径.png`，复用 Hermes 平台适配器。图片渲染或发送失败返回非零及 `error:image_failed`，`sent:true` 表示文本已经送达，`image_sent:false` 表示图片未全部送达；已成功发送的图片记录于 `image_deliveries`。不自动重试整次推送，以免文本重复。

定时推送由部署者自行启用，先通过 `hermes send --list --json` 取得目标。参见 README 的 cron 配置，明确用户指定的渠道与推送时间后再安装。目标 ID、机器路径和定时任务配置留在本地，不要提交到仓库。使用 `flock -n` 可防止任务并发；不自动重试，停机错过时不补发。

需要实际浏览器分页验证时，在可启动 Chrome 的宿主环境运行：
`AGENDA_RENDER_TEST=1 python3 -m unittest discover -s ~/.hermes/skills/productivity/DayLeaf/tests -v`。
