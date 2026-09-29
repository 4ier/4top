# 4top

**所有编程 agent，一个终端。**

找到你的工作，恢复精确的原生会话，用 SSH 触达其他机器。一个基于
**session-ls** 的键盘优先终端面板；没有 4top 常驻服务、不调用模型、不上传遥测。

[English](README.md) · [兼容记录](docs/compatibility.md) · [远程主机](docs/remote-design.md) · [手机/平板](docs/mobile.md) · [验收](docs/validation/README.md)

![4top 合成演示，未读取用户历史](docs/demo/demo.svg)

> 当前为 **0.2.0a7 alpha**。装了 tmux 时，4top 把列表放在它打开的 agent 旁边；它不记录这些
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

打开 `4top`，所有机器同时列出：本机在前，然后是每台已配置的主机；每台机器按最近优先
排列，各自分页。选中会话按 **Enter**：拥有它的那台机器先确认能否在那里恢复，然后直接打开。
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
| `→`, `Alt-←` / `Alt-→` | 切到 agent / 在列表与 agent 之间切换（tmux 布局） |
| `[` / `]` | 光标所在机器的上一页 / 下一页 |
| `/`, `Enter`, `Esc` | 搜索元数据、回到列表、清除搜索和筛选 |
| `p`, `f`, `a` | 只看一个项目；折叠光标所在的机器；显示子会话 |
| `Ctrl-F` | 明确触发全文搜索；`Esc` 取消 |
| `Space`, `i` | 最近的消息（只读，`e` 往前翻）、详情 |
| `n`, `r`, `?` | 在选中的机器上新建 agent、刷新、帮助 |
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
网络访问只有你配置的 ssh，以及每天一次的 PyPI 更新检查（`[ui] update_check = false` 可关闭）。标题和路径本身可能敏感，录屏前仍需检查。

[完整操作与退出码](docs/troubleshooting.md) · [隐私](docs/privacy.md) ·
[贡献](CONTRIBUTING.md) · [兼容性](docs/compatibility.md)

MIT 许可。基于 4ier 的 session-ls，使用 Textual；不隶属于任何 agent 厂商。
