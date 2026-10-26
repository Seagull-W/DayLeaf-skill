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

`add` 的 `title` 必填；`start` 可省略，省略时以请求 `timezone`（默认 `Asia/Shanghai`）中的登记当天日期保存。`start` 接受 `YYYY-MM-DD` 日期或带 UTC 偏移的 ISO 8601 日期时间。`end` 可省略或为 null；有值时必须与 `start` 精度相同且严格晚于开始。`notes` 可省略或为 null。存储字段为 `{id,title,start,end,notes,done,due}`；`done` 为布尔值，添加时可省略，默认 `false`。`due` 可省略或为 null，也接受与 `start` 相同的两种时间形式，精度可独立。文件版本仍为 1，兼容旧日程。

`list` 请求字段：`period`、`from`、`to`、`query`、`status`、`due_before`、`timezone`；`summary` 请求字段：`period`、`status`、`due_before`、`timezone`。`status` 可为 `all`（默认）、`pending` 或 `done`，分别查询全部、未完成或已完成事项。

接口示例（每行代表一次独立调用）：

```json
{"action":"add","title":"整理资料"}
{"action":"add","title":"明天处理","start":"2026-10-27"}
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
- “明天下午三点”依据用户时区转为具体日期、15:00 和对应 UTC 偏移；仅有“上午”“下午”“晚上”“晚点”时按日期精度登记，不推测小时；“周五左右”“三点”若不能确定日期或上午/下午，先询问。“本周五”已过去而用户语境指未来时也需澄清，不能悄悄挪到下周。
- 只有用户实际给出钟点才写入具体时间；未给时间或仅给日期时，保存日期并标注“时间未定”。具体日期时间必须含 UTC 偏移，例如 `+08:00` 或 `Z`。`timezone` 用于查询日期范围和摘要展示，不会替输入时间补偏移。跨 DST 时根据 IANA 时区确认偏移；重复出现或不存在的本地时间先澄清，不能猜测。结束时间未知时省略；不要默认一小时。
- 修改、删除先查询候选。仅在用户意图唯一匹配时使用完整 ID；出现多项或目标不明确，展示标题、日期、时间供用户选择，然后再调用。不能取第一条、按模糊标题批量删除或用 ID 前缀。即便查询只有一项，也不能忽略用户描述与该项不符的情况。
- 只修改用户指定字段。若平移事件，应确定是否保留原时长，并同时传入新的开始和结束时间，避免旧结束时间残留。
- 用户随任务给出操作细节时，按「任务附带信息（notes）」规则保存。
- 添加/修改遇到 `error:conflict` 时尚未保存。告知冲突的标题和时间，询问改时间还是允许重叠；只有用户明确接受重叠后，才重试相同请求并加 `allow_conflict:true`。脚本在锁内重新检查冲突。
- 日期精度事项不参与冲突检测。有具体结束时间的定时事件按 `[start,end)` 检测冲突，相邻事件不冲突；无结束时间视为一个时间点，只检查同一时间点或落入其他事件区间，不假设持续时长。
- 普通对话直接通过当前 Hermes 会话回复；成功后说清具体日期、时间及操作结果。查询今日/本周优先使用 `summary.text`，不要再调用发送工具导致重复消息。失败时不得宣称已保存。
- 文件损坏时脚本拒绝继续写入；保留原文件，由用户从备份恢复，不要清空重建。写入使用同目录临时文件、fsync、原子替换以及稳定的 sidecar 锁，适用于本地文件系统；不要手工并发编辑 JSON。

## 时间未定的事项

用户未指定钟点、只给日期，或只说“上午/下午/晚上/晚点”，均按日期精度登记，不默认 09:00 或其他小时；只有准确时间对用户有实质影响时才追问。没有日期时以登记当天为锚点；例如当前日期为 2026-10-26，用户说“明天整理资料”，保存 `start: "2026-10-27"`。无日期也无钟点时可省略 `start`，脚本使用请求时区的当天日期。

日期事项在当天按当地零点排序，排在当天有钟点的事项之前；不参与冲突检测。文本 `summary` 和卡片都显示“时间未定”，卡片在无结束日期时还显示“结束未定”。跨日事项可给日期形式的 `end`，必须严格晚于 `start`。修改可在日期与具体时间之间转换，同时处理原有 `end`，合并后两者必须精度一致；`end:null` 可清空结束时间，`start` 不可清空。

## 完成状态

- 仅当用户明确说事项已完成或要求标记完成时，先用 `list` 定位唯一完整 ID，再通过 `update` + `changes.done:true` 标记；目标不唯一时先澄清。
- 不因时间过去自动推断完成；日期精度事项同样支持完成状态。不得通过往 `notes` 写入“✅ 已完成”等标记保存完成状态。
- 查询待办使用 `status:"pending"`；取消完成标记使用 `changes.done:false`，只修改用户指定字段。
- `done` 只接受 JSON 布尔值。旧文件缺少该字段时读取为 `false`，读取不改文件，下次写入会持久化该字段；文件版本仍为 1。这是单向归一化。
- 文本摘要在已完成标题前显示 ✅；卡片保留所有事项，已完成项标题加删除线、降低透明度，时间前加 `✓ `；`list` 返回布尔字段。

```json
{"action":"update","id":"查询返回的完整ID","changes":{"done":true}}
{"action":"list","status":"pending"}
```

语义与兼容性说明见 [docs/completion.md](docs/completion.md)。

## 截止时间（due）

- 用户说“截止”“到期”或“deadline”时写入 `due`，不要塞进 `notes` 或 `start`。`start` 仍表示登记或计划执行的日期/时间，例如今天登记的经济学作业，明天 09:00 截止。
- `add` 请求字段 `due` 可省略（默认 null）；接受 `YYYY-MM-DD` 或带 UTC 偏移的 ISO 8601 日期时间。其精度独立于 `start`，日期精度的开始可配具体钟点的截止。
- 截止不得早于开始锚点，允许相等；日期按请求时区当地零点解析。截止不占时间、不会阻挡其他事项，不参与冲突检测，也不改变 `end` 的含义。
- 先查询唯一完整 ID，再用 `update` 的 `changes.due` 调整；`changes.due:null` 清空，只修改用户指定字段。
- `list`、`summary` 的 `due_before` 接受同样的两种时间形式，保留非空截止且解析值小于等于该边界的事项；可与 `status` 叠加。日期边界是当地零点，不代表整天结束。
- 自行查询提醒候选时，可用 `list` 的 `status:"pending"` + `due_before`；不要附加登记日期范围，以免漏掉以前登记的事项。`summary` 仍受 today/week 范围限制。自动提醒使用下述独立脚本。
- 摘要在标题后追加 `  ⏰ 截止 09:00`（当天）或 `10-27 09:00`（其他日期）；日期期限仅显示 `10-27`。卡片在原时间标签后追加 ` · 截止 10/27 09:00`，保持完成标记与行结构；`list` 返回原始 `due`。

```json
{"action":"add","title":"经济学作业","start":"2026-10-26","due":"2026-10-27T09:00:00+08:00"}
{"action":"list","status":"pending","due_before":"2026-10-27T09:00:00+08:00","timezone":"Asia/Shanghai"}
```

旧文件缺少 `due` / `done` 时，在内存中补为 null / false，读取不改文件；下次成功写入持久化，文件版本仍为 1，保持单向归一化。顺序校验与展示说明见 [docs/deadline.md](docs/deadline.md)。

### 截止提醒

技能本身不创建或安排定时任务。部署者可配置外部调度器（如 Hermes cron），约每 10 分钟运行 `scripts/due_watch.py`，无需 LLM。以下仅为 cron 命令模板，替换所有路径为部署环境的绝对路径，Python 解释器路径也须确认：

```cron
*/10 * * * * /usr/bin/python3 /absolute/path/DayLeaf/scripts/due_watch.py --data /absolute/path/agenda/events.json --timezone Asia/Shanghai
```

脚本仅输出文本，不发送消息、访问网络或调用 LLM。带钟点的未完成事项在截止前 2 小时、1 小时、30 分钟三个检查点提醒，每个截止最多三次截止前提醒，每次运行每项最多一行。同一截止首次提醒时，将已错过的检查点合并为一条消息，并将所有已跨过的检查点记为已提醒；此后从该截止最新提醒时间起，默认至少间隔 20 分钟（`--min-gap-minutes` 可设为非负整数分钟），间隔内不输出也不标记，满足间隔后只标记最大的待发检查点，更紧急的档位留待后续运行。实际提醒时间受调度粒度限制；调度缓慢或漏跑可能缩短提醒序列，但不会集中补发多条消息。截止后默认 12 小时宽限内可另发一次“已过期”（使用独立键，不受最小间隔限制），到达宽限边界后不再补报。日期截止按当地零点计算，不适用分钟检查点，仍在截止前两小时内或过期宽限内只提醒一次，只显示日期、不显示分钟倒计时。调度器应逐字投递非空 stdout，不改写标题；空 stdout 表示无须发送，错误写入 stderr 并以非零退出，不将错误当作提醒投递。

默认状态文件为数据文件同目录的 `due-reminders.json`，按事件 ID、原始 `due` 和检查点去重；修改截止后可再次提醒，删除或完成后清理该事件的记录。`--state` 可指定其他状态文件；`--dry-run` 预览且不改状态；`--checkpoints`（默认 `120,60,30`）可调整检查点，`--grace-minutes` 可调整补报宽限，`--now` 接受带偏移的 ISO 时间用于测试。状态记录的是脚本输出，不是渠道送达确认；调度器负责可靠投递。

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
