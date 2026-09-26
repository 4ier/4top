# 4top

**所有编程 agent，一个终端。**

找到你的工作，恢复精确的原生会话，用 SSH 触达其他机器。一个基于
**session-ls** 的键盘优先终端面板；没有 4top 常驻服务、不调用模型、不上传遥测。

[English](README.md) · [兼容记录](docs/compatibility.md) · [远程主机](docs/remote-design.md) · [验收](docs/validation/README.md)

![4top 合成演示，未读取用户历史](docs/demo/demo.svg)

> 当前为 **0.2.0a1 alpha**。当前开发线不持有进程，也不驱动任何多路复用器。
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
.venv/bin/python -m pip install ./packages/session-ls .
.venv/bin/4top --demo
. .venv/bin/activate
4top                       # 浏览本机全部会话
4top new codex             # 在当前终端里启动一个 agent
4top --host build-box      # 通过 ssh 查看另一台机器
```

必须同时提供两个本地包；安装不会假定 `session-ls 0.2.0` 已存在于包注册表。
原始 agent CLI 需单独安装与认证，4top 不代装、不代登录、不主动添加权限绕过选项。
Cursor 转录只读，不提供启动或恢复。

## 日常操作

打开 `4top` 会列出本机全部会话（最近优先）。选中一行按 Enter：4top 先请求确认，
然后在本终端里运行原生 CLI 恢复那个精确会话；退出 agent 后回到面板。
`q` / Ctrl-C 只关闭面板。

裸终端还是你自己已经在用的多路复用器都一样：4top 在启动它的那个终端里运行 agent，
自己不分配任何终端。想让会话活到你合上笔记本之后，就在你已经在用的多路复用器里跑 4top。

| 按键 | 作用 |
| --- | --- |
| `↑` / `↓`, `Enter` | 选择并恢复 |
| `H` | 在面板内切换本机与已配置主机 |
| `/`, `Enter`, `Esc` | 搜索元数据、回到表格、清除或取消 |
| `Ctrl-F` | 明确触发全文搜索；`Esc` 取消 |
| `Space`, `i` | 只读预览、详情 |
| `n`, `r`, `?` | 新建 agent、刷新、帮助 |
| `q`, `Ctrl-C` | 只关闭面板 |

普通文字匹配支持中文和带引号的词组，多个词按 AND 匹配，不执行正则或历史内容。
全文搜索只读已配置来源，明确报告部分扫描结果。

## 会话，不是进程

原生 agent 本质上是一个文件：`pi --session <path>`、`claude --resume <id>`、
`codex resume <id>` 都能只凭转录恢复，所以**转录是持久的，进程是瞬时的**——
4top 因此只跟踪转录。

恢复（Resume）会用精确的 ID 或源文件路径创建**新进程**，无法恢复已丢失的内存、
后台子进程、网络连接或已销毁的机器。恢复使用 CLI **当前的原生配置**，
不重放最初启动参数；发送下一个任务前请自行检查原生权限。

因为 4top 不持有进程，它也不对存活状态做任何声明：一行就是一个可以恢复的会话，仅此而已。
查询失败会明确报错，而不会显示成一台空机器。

两个必须知道的后果：恢复出的 agent 是新进程；同目录多 agent **没有工作树隔离**。
另外 `4top new` 在前台运行 agent——如果当时不在多路复用器里，它会随终端一起结束。

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
4top --host build-box              # 整个面板切到该主机
4top --host build-box list --json
4top --host me@10.0.0.4 doctor     # 未配置的目标也可直接用
```

视图是**隔离**的：默认只显示本机，主机是替换而不是把多台机器合并成一张表。
`H` 让你在面板里直接切换，`--host NAME` 则是启动时就切好。
任何会启动进程的操作都通过 `ssh -t` **在那台主机上**执行，所以恢复出来的 agent 就
待在它历史所在的地方；远端 CLI 干活，本地只负责把终端让出去。连接复用
（ControlMaster）让刷新保持廉价，BatchMode 让缺少密钥时快速失败而不是卡在提示上，
行 schema 不一致的远端会被拒绝而不是部分解析。

## 命令行

```sh
4top list --json                          # 每行一个 JSON 对象
4top list --agent pi --project 4top
4top search 'retry "database timeout"'    # 元数据匹配
4top search '中文' --full                  # 解码后的全文搜索
4top preview h_<key>                      # 一页只读摘录
4top check h_<key> --json                 # 这个会话在这里能不能恢复，不能则给出原因
4top new codex -- --model MODEL           # 原生参数放在 -- 之后
4top resume h_<key> --yes                 # 把这个进程换成该 agent
4top doctor --json
```

`--config`、`--host`、`--no-color` 放在子命令前后均可。key 只有在前缀无歧义时
（至少四个字符）才允许缩短；行号永远不是执行目标。`list --json` 每行包含
`schema_version`、`key`、`agent`、`host`、`cwd`、`title`、`started`、`last`、
`source`、`status`、`can_resume`。

## 配置与隐私

可选配置位于 `~/.config/4top/config.toml`，支持 XDG 目录、`--config`、
`--host`、`--no-color` / `NO_COLOR`。原生目录支持 `CODEX_HOME`、
`CLAUDE_CONFIG_DIR`、`PI_CODING_AGENT_DIR` 和显式配置覆盖。配置示例见英文 README。

本地状态保持私有：`$XDG_STATE_HOME/4top` 里只有本机身份和上次选中项，
`$XDG_CACHE_HOME/4top` 是可重建的元数据缓存。不保留环境变量值、prompt 文本或转录内容。
唯一的网络访问就是你配置的 ssh。标题和路径本身可能敏感，录屏前仍需检查。

[完整操作与退出码](docs/troubleshooting.md) · [隐私](docs/privacy.md) ·
[贡献](CONTRIBUTING.md) · [兼容性](docs/compatibility.md)

MIT 许可。基于 4ier 的 session-ls，使用 Textual；不隶属于任何 agent 厂商。
