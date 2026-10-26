# DayLeaf 🍃

一个供 **Hermes Agent** 使用的轻量级日程管理 Skill。通过微信、QQ 等聊天渠道说出安排，由 Agent 理解自然语言、调用本地 Python 脚本管理日程，并生成适合手机阅读的每日卡片。

**本地 JSON · Python 标准库 · HTML/CSS 卡片 · 复用 Hermes 推送**

## 卡片预览

以下图片使用虚构示例日程，展示浅色和深色主题。

| 浅色 | 深色 |
| --- | --- |
| ![浅色卡片](previews/agenda-light-01.png) | ![深色卡片](previews/agenda-dark-01.png) |

720 × 1200 竖屏卡片包含日期、星期、当天日程数量与起止时间。中文标题自动换行；日程过多时按实际排版高度分页，超长单项跨页延续，避免缩小字体或截断内容。

## 功能

- 添加、查询、修改、删除日程；唯一 ID、标题、日期或开始时间、可选结束时间和备注。添加时可省略 `start`，按请求时区的登记当天保存日期。
- 任务附带信息（步骤、参数、公式、链接等）写入备注；没有附带信息时留空。规则见 [docs/task-notes.md](docs/task-notes.md)。
- JSON stdin/stdout CLI，适合 Agent 直接调用。
- 今日、本周纯文本摘要；默认 `Asia/Shanghai`，支持 IANA 时区。
- 有钟点的日程冲突默认拒绝保存；用户明确接受后才允许重叠。日期精度事项不参与冲突检测，摘要和卡片显示“时间未定”。
- 修改、删除必须使用完整 ID；目标不明确时 Agent 先向用户澄清。
- 本地文件锁、临时文件、`fsync` 与原子替换，避免并发写入丢失。
- 每日先发送文本摘要，再发送图片；图片失败时已送达的文本仍可阅读。

不包含 Web 管理端、数据库、账户系统、循环事件或逐项提醒服务。同一 Hermes profile 下的渠道共用一份个人日程。

## 环境要求

- Linux 或 macOS，Python **3.9+**，系统 IANA 时区数据。
- 已安装并配置消息渠道的 Hermes Agent，支持 `hermes send`。
- 生成图片需要已安装的 Chrome/Chromium 和中文字体（例如 Noto Sans CJK 或文泉驿正黑）。仅管理日程或发送纯文本不需要浏览器。
- 微信、QQ 等插件平台可能依赖运行中的 Hermes gateway、有效会话及平台发送权限。

Python 代码仅使用标准库，不需要 `pip install`。使用 POSIX `fcntl` 文件锁，不直接支持原生 Windows。

## 安装

```bash
git clone https://github.com/Seagull-W/DayLeaf-skill.git \
  "${HERMES_HOME:-$HOME/.hermes}/skills/productivity/DayLeaf"
```

在 Hermes 新会话中使用 DayLeaf；如果当前会话尚未发现 Skill，刷新 Skill 列表或启动新会话。Agent 的操作规范见 [SKILL.md](SKILL.md)。

默认数据存放于 `${HERMES_HOME:-~/.hermes}/agenda/events.json`，独立于 Skill 仓库。可通过 `--data` 指定其他文件。多 profile 部署时，应让脚本数据路径和 Hermes 发送进程使用同一 `HERMES_HOME`。

## CLI 使用

```bash
SKILL_DIR="${HERMES_HOME:-$HOME/.hermes}/skills/productivity/DayLeaf"

python3 "$SKILL_DIR/scripts/agenda.py" <<'JSON'
{"action":"add","title":"项目讨论","start":"2027-01-18T09:00:00+08:00","end":"2027-01-18T10:00:00+08:00","notes":"带上草稿"}
JSON

python3 "$SKILL_DIR/scripts/agenda.py" <<'JSON'
{"action":"add","title":"整理资料","start":"2027-01-20"}
JSON

python3 "$SKILL_DIR/scripts/agenda.py" <<'JSON'
{"action":"summary","period":"today","timezone":"Asia/Shanghai"}
JSON
```

每次调用读入一个 JSON 对象，返回一个 JSON 对象。更多请求示例：

```json
{"action":"list","query":"项目"}
{"action":"list","period":"week"}
{"action":"update","id":"完整日程ID","changes":{"title":"方案讨论"}}
{"action":"delete","id":"完整日程ID"}
```

成功返回 `ok:true`，退出码 0；校验、冲突或读写失败返回 `ok:false`，退出码 1。CLI 参数错误退出码 2。遇到冲突，数据尚未保存；明确接受后，在添加或修改请求中加入 `"allow_conflict":true` 重试。

`start` 可省略，默认保存请求 `timezone`（默认 `Asia/Shanghai`）中的登记当天日期。支持日期 `YYYY-MM-DD`（如 `2027-01-20`）或带明确 UTC 偏移的日期时间（如 `2027-01-18T09:00:00+08:00`）。`end` 可省略或为 null；有值时须与 `start` 精度相同并严格晚于开始。事件字段为 `{id,title,start,end,notes,done,due}`，`done` 只接受 JSON 布尔值，添加时默认 `false`。`due` 是可选截止时间，默认 null，接受与 `start` 相同的两种形式且精度独立。文件版本仍为 1，旧文件可继续使用。

自然语言由 Agent 解释；只给日期、未给时间或仅说“晚点”“上午/下午/晚上”时保存日期，摘要和卡片显示“时间未定”，不猜测小时。日期事项按当地零点排序，不参与冲突检测。结束时间未知时省略，不默认一小时；有钟点但无结束时间的日程按时间点检测冲突。

## 完成状态

用户明确表示完成后，先用 `list` 定位唯一完整 ID，再标记 `done:true`；取消标记用 `done:false`。不根据时间过去自动完成，也不把完成标记写进 `notes`。日期精度事项同样适用。

`list` 和 `summary` 支持 `status:"all"`（默认）、`"pending"`、`"done"` 筛选。摘要在已完成标题前显示 ✅；卡片作为当天记录保留所有事项，已完成标题显示删除线、降低透明度，时间前显示 `✓ `。`list` 返回原始标题和布尔字段。

```bash
# 先查询并确定唯一完整 ID，再替换示例 ID
python3 "$SKILL_DIR/scripts/agenda.py" <<'JSON'
{"action":"update","id":"查询返回的完整ID","changes":{"done":true}}
JSON

python3 "$SKILL_DIR/scripts/agenda.py" <<'JSON'
{"action":"list","status":"pending"}
JSON
```

旧文件缺少 `done` 时读取为 `false`，读取不会改写文件，下次写入会持久化该字段；这是单向归一化。详见 [docs/completion.md](docs/completion.md)。

## 截止时间

`due` 表示到期时间，`start` 保留登记或计划执行时间，`end` 表示执行结束。日期精度的 `start` 可以配带钟点的 `due`，例如今天登记、明天 09:00 截止的经济学作业。截止不参与冲突检测。`due` 可为 null、`YYYY-MM-DD` 或带 UTC 偏移的 ISO 8601 日期时间；不得早于开始锚点，允许相等，日期按请求时区当地零点解析。用 `changes.due` 调整，传 null 清空。

```bash
python3 "$SKILL_DIR/scripts/agenda.py" <<'JSON'
{"action":"add","title":"经济学作业","start":"2026-10-26","due":"2026-10-27T09:00:00+08:00"}
JSON

python3 "$SKILL_DIR/scripts/agenda.py" <<'JSON'
{"action":"list","status":"pending","due_before":"2026-10-27T09:00:00+08:00","timezone":"Asia/Shanghai"}
JSON
```

`list`、`summary` 可用 `due_before` 筛出截止非空且小于等于上限的事项，再与 `status` 组合；上限接受相同两种时间形式，日期上限表示当地零点。自动提醒可使用下述 `due_watch.py`。提醒查询使用不带登记日期范围的 `list`，避免遗漏旧任务；`summary` 仍限于 today/week。

文本摘要在标题后加 `  ⏰ 截止 09:00`（当天）、`  ⏰ 截止 10-27 09:00`（其他日期）或 `  ⏰ 截止 10-27`（日期期限）。卡片在现有时间标签后加 ` · 截止 10/27 09:00`（当天仅显示钟点，日期期限仅显示月日），保留完成标记与分页结构。`list` 返回原始 `due`。

旧文件缺少 `due` / `done` 时读取为 null / false，读取不写文件，下次成功写入持久化，保持单向归一化。UTC 读取校验及完整规则见 [docs/deadline.md](docs/deadline.md)。

### 截止提醒

技能本身不安排定时任务。外部调度器（如 Hermes cron）可每 10 分钟运行以下命令；将路径替换为本机绝对路径，并确认 Python 解释器路径：

```cron
*/10 * * * * /usr/bin/python3 /absolute/path/DayLeaf/scripts/due_watch.py --data /absolute/path/agenda/events.json --timezone Asia/Shanghai
```

带钟点的未完成事项在截止前 2 小时、1 小时、30 分钟三个检查点提醒，每个截止最多三次截止前提醒，每次运行每项最多一行。同一截止首次提醒时，将已错过的检查点合并为一条消息，并将所有已跨过的检查点记为已提醒；此后从该截止最新提醒时间起，默认至少间隔 20 分钟（`--min-gap-minutes` 可设为非负整数分钟），间隔内不输出也不标记，满足间隔后只标记最大的待发检查点，更紧急的档位留待后续运行。实际提醒时间受调度粒度限制；调度缓慢或漏跑可能缩短提醒序列，但不会集中补发多条消息。截止后默认 12 小时宽限内可另发一次“已过期”（使用独立键，不受最小间隔限制），到达宽限边界后不再补报。日期截止按当地零点计算，不适用分钟检查点，仍在截止前两小时内或过期宽限内只提醒一次，只显示日期、不显示分钟倒计时。脚本只打印，无网络、无发送、无 LLM；调度器逐字投递非空 stdout，空 stdout 不发送，stderr 用于错误。默认 `due-reminders.json` 位于数据文件旁，按 ID、原始截止值和检查点去重；`--dry-run` 只预览、不更新状态。更多参数及投递约定见 [截止文档](docs/deadline.md#提醒)。

## 任务附带信息

`notes` 用来放任务自己的执行细节，而不是闲聊补充。用户安排任务时一并给出步骤、参数、公式、参考链接或验收标准，Agent 就把这些内容结构化写入 `notes`；只给标题和时间时留空。用户也可以事后指定某个任务补写、追加、替换或清空备注。

```json
{"action":"add","title":"复现 qwen 神经元转向","start":"2027-01-19T09:00:00+08:00","notes":"配方：L14 N8949 +1、L13 N7956 -1\n结束时间暂定"}
{"action":"update","id":"完整日程ID","changes":{"notes":null}}
```

备注出现在文本摘要的 📝 行，不进入卡片：卡片是当天时间线速览，长文本会破坏排版。单条建议不超过 2000 字符，超长时保留标识符、参数、公式与验收标准并注明截断；原文中的数字和公式逐字保留。不要在备注里写密码、token、卡号等凭证。

完整规则、修改示例和隐私说明见 [docs/task-notes.md](docs/task-notes.md)。

## 卡片与推送

```bash
# 只生成预览，不发送消息
python3 "$SKILL_DIR/scripts/daily_summary.py" --dry-run --theme light
python3 "$SKILL_DIR/scripts/daily_summary.py" --dry-run --theme dark

# 查看当前 Hermes 可用目标（输出可能含私人聊天 ID，请勿公开）
hermes send --list --json

# 替换为你自己的目标，再执行真实推送
python3 "$SKILL_DIR/scripts/daily_summary.py" \
  --to 'PLATFORM:CHAT_ID' --theme light
```

- `--chrome /path/to/chrome`：指定浏览器；默认自动寻找 Chrome/Chromium。
- `--hermes /path/to/hermes`：指定 Hermes CLI；定时任务建议使用绝对路径。
- `--output-dir /path/to/cards`：指定卡片目录；默认是数据文件旁的 `cards/YYYY-MM-DD/`。
- `--timezone Asia/Shanghai`：控制当天范围和显示时区。
- `--text-only`：跳过图片渲染，仅发送文本；可与 `--dry-run` 合用。

推送复用 `hermes send --to ... --file - --json` 和 `MEDIA:/绝对路径.png`，没有独立消息服务。卡片不显示备注，文本摘要保留备注。文本、图片使用同一次日程快照。

图片失败时返回 `ok:false`、`error:image_failed` 和非零退出码。`sent:true` 表示文本已送达；`image_sent:true` 表示图片全部送达。不要自动重试整次调用，以免产生重复消息。图片和文本仍受平台会话、大小及发送频率限制。

## 每日自动发送

Linux 可用已有 cron，每天运行同一个入口。先确认以下信息：

```bash
command -v python3 hermes google-chrome
# 检查主机时区；cron 的 08:00 使用主机调度时区
date '+%Z %z'
crontab -e
```

下面是**模板**，须替换所有 `/ABS/PATH/` 路径和 `PLATFORM:CHAT_ID`：

```cron
0 8 * * * HERMES_HOME=/ABS/PATH/.hermes PATH=/usr/local/bin:/usr/bin:/bin /usr/bin/flock -n /ABS/PATH/dayleaf.lock /usr/bin/python3 /ABS/PATH/DayLeaf/scripts/daily_summary.py --timezone Asia/Shanghai --theme light --hermes /ABS/PATH/hermes --chrome /ABS/PATH/chrome --to 'PLATFORM:CHAT_ID' >> /ABS/PATH/dayleaf.log 2>&1
```

`--timezone` 只控制摘要日期，不会修改 cron 的触发时区。目标目录需要可写。macOS 默认没有 `flock`，可使用本机调度机制调用脚本。

也可将相同命令放入 systemd oneshot service，并使用 `OnCalendar=*-*-* 08:00:00 Asia/Shanghai` 的 timer。两种调度方式只选一种；默认不自动重试或补发。脚本本身不做跨次调用去重。

## 测试

```bash
python3 -m unittest discover -s tests -v

# 可选：真实 Chrome 分页与 PNG 检查（该测试当前使用 google-chrome）
AGENDA_RENDER_TEST=1 python3 -m unittest discover -s tests -v
```

测试使用临时目录和模拟发送，不触碰真实日程，不向渠道发送消息。真实浏览器测试覆盖分页、超长中文/英文标题、文字完整性和图片尺寸。

## 项目结构

```text
SKILL.md                  Agent 调用和对话规则
scripts/agenda.py         本地存储、校验、CRUD 与文本摘要
scripts/daily_summary.py  每日文本/图片推送入口
scripts/render_card.py    本地 Chrome 渲染
templates/card.html      卡片 HTML/CSS 模板
tests/                   标准库 unittest 测试
docs/                    补充说明文档（任务附带信息等）
previews/                虚构示例日程的主题预览
```

## 隐私与维护

仓库不包含真实日程、聊天 ID、渠道凭据、个人机器路径或发送日志；预览图片使用虚构数据。`.gitignore` 排除常见运行数据与本地配置。

配置和日志留在本机；不要提交 `.env`、Hermes 配置、渠道目录、实际 cron 配置或生成的真实卡片。HTML 和 PNG 同样包含日程内容，应按个人数据管理。默认卡片目录不自动清理，可按需删除旧日期产物。

写入保护依赖本地文件系统及所有写入者遵守同一文件锁，不建议将数据文件放在网络文件系统。JSON 损坏时会拒绝覆盖，请先保留文件并从备份恢复。
