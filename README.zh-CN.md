# 4top

**所有编程 agent，一个终端。**

找到你的工作，恢复精确的原生会话，用 SSH 触达其他机器。一个基于
**session-ls** 的键盘优先终端面板；没有 4top 常驻服务、不调用模型、不上传遥测。

[English](README.md) · [兼容记录](docs/compatibility.md) · [远程主机](docs/remote-design.md) · [手机/平板](docs/mobile.md) · [验收](docs/validation/README.md)

![4top 合成演示，未读取用户历史](docs/demo/demo.svg)

> 当前为 **0.2.0a8 alpha**。装了 tmux 时，4top 把列表放在它打开的 agent 旁边；它不记录这些
> agent 的任何状态，哪些已打开一律现问 tmux。
> Codex **0.155.1** 在早期版本上通过了已认证的精确恢复冒烟；
> Claude Code、Pi 与其他原生版本未认证。
> [验收记录](docs/validation/macos-0.1.0a2.md)。已以 pre-release 形式发布到 PyPI：`uv tool install 4top`。

## 从当前源码安装

需要 macOS / Linux 与 **Python 3.11+**，不需要任何终端多路复用器。实际测试过的
版本以兼容记录为准，而非这里的下限。原生 Windows 不支持；可在 Linux/WSL 中尝试。

```sh
git clone https://github.com/4ier/4top.git
cd 4top
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/4top --demo
. .venv/bin/activate
4top                       # 浏览本机全部会话
4top new codex             # 在当前终端里启动一个 agent
4top --host build-box      # 通过 ssh 查看另一台机器
```

`session-ls` 是普通的 PyPI 依赖，会随安装自动获取。原始 agent CLI 需单独安装与认证，4top 不代装、不代登录、不主动添加权限绕过选项。
Cursor 转录只读，不提供启动或恢复。

## 日常操作

打开 `4top` 先看到**"现在"**：所有机器本周的会话合成一个列表，最紧急的在前：`‼ needs you`
（agent 卡在权限提示或提问上，由主机读它的屏幕得知）、`✓ done`（上次打开或查看之后又做完的）、`⟳ working`、`✗ stopped`，
然后是本周其余的。静音的、脚本启动的（`claude -p`、`codex exec`）和子会话不在其中；`/` 搜索全部。
按 `g` 切换成按机器分组、各自分页。选中会话按 **Enter**：拥有它的那台机器先确认能否在那里恢复，然后直接打开。
没有确认弹窗；如果不能打开（缺少 CLI、目录已不存在），原因直接显示在列表里。

装了 **tmux** 时，4top 运行在自己独立的 tmux 服务里，把 agent 打开在列表右侧，就像编辑器里
文件树旁边的文件。再打开别的会话时，之前的会继续在后台运行；`●` 标记已打开的会话，对它按
Enter 会把它切回来。`Alt-←` / `Alt-→`（或在列表里按 `→`、或直接点击）在列表和 agent 之间切换。
在手机这类窄屏上，获得焦点的一侧占满屏幕。**`q` 是脱离**：agent 继续运行，再运行 `4top` 就能
接回原来的样子；`Q` 关闭全部，但常驻在远程主机上的 agent（见下文）只是不再在这里显示。

没有 tmux 或设置 `layout = "plain"` 时，Enter 在当前终端里运行 agent，退出后回到列表。

| 按键 | 作用 |
| --- | --- |
| `↑` / `↓`, `Enter` | 选择、打开（已打开则切过去） |
| `g` | 在"现在"（所有机器、最紧急的在前）和按机器分组之间切换 |
| `v`, `c` | 查看正在运行的 agent 屏幕并直接回复；一行快速回复 |
| `y`, `d` | 批准或拒绝 agent 正在等待的权限提示 |
| `n` | 新任务：机器、最近的项目、agent、要做的事；常驻在那台主机上 |
| `R`, `x`, `X` | 给会话起名；把它或整个项目从"现在"里静音 |
| `→`, `Alt-←` / `Alt-→` | 切到 agent / 在列表与 agent 之间切换（tmux 布局） |
| `[` / `]`, `f` | 上一页 / 下一页、折叠（按机器视图） |
| `/`, `Enter`, `Esc` | 搜索全部、回到列表、清除搜索和筛选 |
| `p`, `A` | 只看一个项目；同时显示脚本启动和子会话 |
| `Ctrl-F` | 明确触发全文搜索；`Esc` 取消 |
| `Space`, `i` | 最近的消息（只读，`e` 往前翻）、详情 |
| `r`, `?` | 刷新、帮助 |
| `q`, `Q`, `Ctrl-C` | 脱离（tmux）或退出；关闭全部 agent 并退出 |

每一行根据转录的末尾显示会话在做什么：`⟳ working` 表示 agent 正在执行，
`▶ your turn` 表示它已经把回合交还给你，`✗ stopped` 表示执行中途超过十分钟没有动静。
如果主机上的 tmux 表示这个会话的 agent 正在那里运行，而本面板没有显示它，行首标 `○`；
这样的会话不会被标成 stopped：长时间的工具调用只是安静，不是停了。
如果你最后一次的要求和开场白不同，会在第三行 `› …` 显示，项目名后面还会带上 git 分支。
状态标记只针对最近一天内的会话，更早的视为历史。

agent 为自己启动的会话（Codex 的审批复核、派生的 worker）默认隐藏，按 `a` 显示。
预览从最近的消息开始，而不是转录开头那段注入的上下文。

普通文字匹配支持中文和带引号的词组，多个词按 AND 匹配，不执行正则或历史内容。
全文搜索只读已配置来源，明确报告部分扫描结果。

## 会话，不是进程

原生 agent 本质上是一个文件：`pi --session <path>`、`claude --resume <id>`、
`codex resume <id>` 都能只凭转录恢复，所以**转录是持久的，进程是瞬时的**——
4top 因此只跟踪转录。

恢复（Resume）会用精确的 ID 或源文件路径创建**新进程**，无法恢复已丢失的内存、
后台子进程、网络连接或已销毁的机器。恢复使用 CLI **当前的原生配置**，
不重放最初启动参数；发送下一个任务前请自行检查原生权限。

4top 也不记录任何进程信息。在 tmux 布局里，每个 pane 标记着它运行的会话，`●` 只表示 tmux
此刻有这个 pane，除此之外什么都不记。
查询失败会明确报错，而不会显示成一台空机器。

两个必须知道的后果：恢复出的 agent 是新进程；同目录多 agent **没有工作树隔离**。
不在 tmux 布局里时，`4top new` 在当前终端前台运行 agent，会随终端一起结束。

## 通过 SSH 查看远程主机

只要你能 `ssh` 上去，就能把它接进 4top。远端不需要安装任何服务，不开放端口，
也不保存凭据：远端就是同一个 CLI，本地 4top 只是去运行它。

```toml
# ~/.config/4top/config.toml
[hosts.build-box]
ssh = "me@build-box"                 # 任意 ssh 目标，包括 tailnet 名称
# command = "/opt/4top/bin/4top"     # 非登录 shell 的 PATH 里没有 4top 时指定
# refresh_seconds = 15.0
```

```sh
4top                               # 本机和所有已配置的主机
4top --host build-box              # 面板只看这台主机
4top --host build-box list --json
4top --host me@10.0.0.4 doctor     # 未配置的目标也可直接用
```

每台已配置的主机在列表里有自己的一组，按各自的间隔增量刷新，没有变化时只传几百字节。
上次看到的内容会缓存，所以主机能立刻显示再慢慢追上；连不上的主机保留原有内容并标明不可达。
任何会启动进程的操作都通过 `ssh -t` **在那台主机上**执行，所以恢复出来的 agent 就
待在它历史所在的地方；远端 CLI 干活，本地只负责把终端让出去。连接复用
（ControlMaster）让刷新保持廉价，BatchMode 让缺少密钥时快速失败而不是卡在提示上，
行 schema 不一致的远端会被拒绝而不是部分解析。

**agent 常驻在它所在的主机上。** 主机装了 tmux 时，agent 运行在那台主机上 4top 专用的
tmux 服务里（`4top-agents`，与你自己的 tmux 分开），面板的 ssh 连接只是接入它
（`4top attach`）。断网、终端 App 被杀、面板关闭，都只是断开接入，agent 继续干活。再次打开
这个会话，无论从同一台设备还是另一台，都会接回同一个进程，而不是再启动一个。这个 tmux 服务
没有状态栏、没有前缀键，所有按键都交给 agent。ssh 连接带存活探测，断掉的链路大约 45 秒后
显示为 "Disconnected"，而不是卡住。运行旧版 4top 的主机仍按原来的方式恢复；没有 tmux 的
主机上，agent 仍在 ssh 会话里运行。本机的会话仍在面板自己的布局里打开，但已经常驻在本机的
（从另一台设备打开的）会被接入，而不是再启动一个。

## 手机通知

可选、按主机开启、不需要常驻进程：agent 在需要你或完成时本来就会调用外部程序，
`4top notify --install` 把这些调用接到 4top，由它向你手机订阅的 [ntfy](https://ntfy.sh)
主题发一条消息。

```sh
4top notify --install     # 接好本机的 agent，并打印要订阅的主题
4top notify --test        # 发一条测试消息
4top notify --uninstall   # 只移除 --install 加上的内容
```

没有配置主题时，`--install` 在公共的 ntfy.sh 上生成一个随机主题，并写入本机配置的
`[notify] url`；`--url` 可以指定你自己的（自建服务器，或者让每台主机用同一个主题）。
在 ntfy 安卓 App 里点 **+**，订阅它打印的主题。每台主机都要各自运行一次：通知由各自
主机上的 hook 发出。`--agent claude`（可重复）只接入或移除指定的 agent。

消息标题是 `主机 · 项目`，正文是面板里同样叫法的状态，加上 agent 说的话和你最近的请求：

- **Needs you (permission / question)**，高优先级：Claude Code 请求运行工具或向你提问
  （`Notification` hook）；Pi 扩展弹出的提示。
- **Done**：这一轮结束（Claude Code `Stop`、Codex `notify`、Pi `agent_settled`）。
  Codex 只把结束的回合告诉 notify 程序，所以 Codex 不会发 “needs you”。
- **Error**：这一轮失败（Claude Code `StopFailure`、Pi 报错）。

接入的位置：Claude Code `settings.json` 里的 hooks，Codex `config.toml` 顶层的 `notify`
程序（Codex 原有的 notify 程序会继续运行：4top 先调用它），以及 Pi `extensions/` 里的一个
扩展文件。每个文件第一次修改前会复制为 `*.4top-backup`；不是 4top 写的条目一律不动。
hook 立即返回，由一个脱离的子进程发送，最多等五秒，所以服务器不可达也不会拖住或弄坏
agent。重复会被丢弃：同一会话十分钟内相同的消息，或二十秒内的任何消息（除非它刚刚变成
需要你）。脚本启动的会话（`claude -p`、`codex exec`）和其他 agent 启动的会话不会通知
（以主机上 session-ls 能区分出来为限）。

在公共服务器上，主题是唯一的秘密：知道它的人可以读到这些消息（主机名、项目名、请求和
agent 的最后一句话），也可以往里发。点通知打开的是 ntfy 而不是 Termux：ntfy 只能打开链接，
而 Termux 没有注册能打开它的链接（只有广播给 Tasker 这类自动化 App 才行）。
## 云端任务（E2B）

干活的主力是家里的机器；它们休眠或忙不过来时，可以把任务派到云端：一个专属的
[E2B](https://e2b.dev) 沙箱。只需要仓库地址就能发起，所以从任何地方都能派，平板也可以。

```sh
4top cloud new claude "fix the flaky retry test" --repo 4ier/app --ref main
4top cloud ls                      # 状态、已花费、剩余时长、今天的花费
4top cloud open app                # 查看它的 agent，或和它对话
4top cloud done app --pr           # 推送分支 4top/app，开 PR，结束沙箱
4top cloud pause app
4top cloud rm app                  # 成果还没在 4top/app 上时拒绝；--discard 强制
```

一个任务 = 仓库 + ref + prompt。沙箱自己克隆仓库，新建分支 `4top/NAME`；agent 带着 prompt
在沙箱自己的 agent tmux 里启动，和任何常驻 agent 的主机一样。没人接入时它照样干活，在面板里
对它那一行按 Enter 就用 `4top attach` 接入。产出是推送上去的分支。`done` 提交 agent 留下的
改动并推送，把沙箱快照保留 `keep_snapshot_days` 天，然后销毁沙箱。在某个克隆目录里运行时，
`new` 默认用它的 origin 和当前分支，以已推送的为准；只在本设备上的改动不会带过去。

费用有上限。沙箱运行不会超过 `max_minutes`；到点后它会暂停，成果保留，之后只有 `done` 或
`rm` 能唤醒它。如果一个任务在最坏情况下会让今天的花费超过 `daily_budget_usd`，它在创建
任何资源之前就会被拒绝。花费读自 E2B 自己记录的每个 4top 沙箱的运行时段，涵盖所有设备，
按 E2B 公布的价格计算（2026-09-30 查得：每 vCPU 秒 $0.000014，每 GiB 秒 $0.0000045），
所以是估算，以 E2B 的账单为准。暂停的沙箱不会被流量唤醒，刷新永远不花钱；只有你主动的操作
（比如打开它）才会唤醒它。

```toml
[cloud]                   # 有这一节时，面板也会列出正在运行的任务
template = "4top"         # 用 contrib/e2b 构建
max_minutes = 60
daily_budget_usd = 5.0
keep_snapshot_days = 7
```

凭证来自发起任务的设备，只交给这一个任务：E2B key（`E2B_API_KEY` 或 `e2b auth login`）、
GitHub token（`GH_TOKEN`、`GITHUB_TOKEN` 或 `gh auth token`），以及 agent 自己的凭证。
Claude 用 `claude setup-token` 得到的 `CLAUDE_CODE_OAUTH_TOKEN`（或
`~/.config/claude-code/oauth-token`）或 `ANTHROPIC_API_KEY`；Codex 用 `OPENAI_API_KEY`
或 `auth.json`。GitHub token 只在克隆和推送时交给 git，从不写入磁盘。agent 的凭证只存在于
它的进程里，`done` 在快照之前会停掉 agent 并删除这些凭证。E2B key 从不进入沙箱。设备靠
ssh 公钥登录沙箱，本机需要 `websocat`（`brew install websocat`、`pkg install websocat`）。
沙箱里的 agent 执行命令前不再逐条询问，因为沙箱里除了这个任务什么都没有。

也可以把某个沙箱固定成主机。刷新永远不会唤醒它；打开其中一行会唤醒它，直到它的生命期结束：

```toml
[hosts.scratch]
e2b = "SANDBOX_ID"
```

设计见 [docs/e2b-design.md](docs/e2b-design.md)。

## 命令行

```sh
4top list --json                          # 每行一个 JSON 对象
4top list --agent pi --project 4top
4top search 'retry "database timeout"'    # 元数据匹配
4top search '中文' --full                  # 解码后的全文搜索
4top preview h_<key>                      # 一页只读摘录
4top preview h_<key> --tail               # 改为最近的消息
4top check h_<key> --json                 # 这个会话在这里能不能恢复，不能则给出原因
4top new codex -- --model MODEL           # 原生参数放在 -- 之后
4top resume h_<key> --yes                 # 把这个进程换成该 agent
4top attach h_<key>                       # 本机专用 tmux 里的 agent；没有就先启动
4top new codex --resident                 # 新 agent 也常驻在那里
4top notify --install                     # 通过 ntfy 推送通知（见上文）
4top doctor --json
```

`--config`、`--host`、`--no-color` 放在子命令前后均可。key 只有在前缀无歧义时
（至少四个字符）才允许缩短；行号永远不是执行目标。`list --json` 每行包含
`schema_version`、`key`、`agent`、`host`、`cwd`、`title`、`started`、`last`、
`source`、`status`、`can_resume`、`resident`（此刻这个会话有 agent 在本机专用的 tmux
里运行）。

## 配置与隐私

可选配置位于 `~/.config/4top/config.toml`，支持 XDG 目录、`--config`、
`--host`、`--no-color` / `NO_COLOR`。原生目录支持 `CODEX_HOME`、
`CLAUDE_CONFIG_DIR`、`PI_CODING_AGENT_DIR` 和显式配置覆盖。配置示例见英文 README。
`[ui] layout` 选择 tmux 布局或纯终端，`rows_per_host` 固定每台机器每页的会话数。
`[agents.NAME] args` 会加到该 agent 的每次启动和恢复上（例如权限模式，4top 默认不加任何参数）；
远程主机要在那台主机自己的配置里设置，因为命令是远端的 4top 生成的。

本地状态保持私有：`$XDG_STATE_HOME/4top` 里只有本机身份和上次选中项，
`$XDG_CACHE_HOME/4top` 是可重建的元数据缓存。不保留环境变量值、prompt 文本或转录内容。
网络访问只有你配置的 ssh，以及每天一次的 PyPI 更新检查（`[ui] update_check = false` 可关闭）；
开启通知后，每条通知再向你的 ntfy 主题发一次 POST（`[notify] events` 可只选部分状态）。标题和路径本身可能敏感，录屏前仍需检查。

[完整操作与退出码](docs/troubleshooting.md) · [隐私](docs/privacy.md) ·
[贡献](CONTRIBUTING.md) · [兼容性](docs/compatibility.md)

MIT 许可。基于 4ier 的 session-ls，使用 Textual；不隶属于任何 agent 厂商。
