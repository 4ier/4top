# 4top

**查看现场，接回进程，恢复工作。**

一个围绕 tmux + session-ls 的终端面板。统一查看编程 agent 的运行现场和
原生历史，不增加 4top 常驻服务，不调用模型，不上传遥测。

[English](README.md) · [兼容记录](docs/compatibility.md) · [验收](docs/validation/README.md)

![4top 合成演示，未读取用户历史](docs/demo/demo.svg)

> 当前为 **0.1.0a2 alpha**。Mac 真机通过 109 项自动化测试；
> Claude Code **2.1.280**、Codex **0.155.1** 另行通过了已认证的原生接入、
> 分离和精确恢复验证。[验收记录](docs/validation/macos-0.1.0a2.md)。
> 驱动仍为实验性，Pi 与其他原生版本未认证；此版本尚未发布到 PyPI。

## 从当前源码安装

需要 macOS / Linux、Python 3.11+，以及 tmux（最低支持目标 3.3，实际测试
版本以兼容记录为准）。原生 Windows 不支持；可在 Linux/WSL 环境中尝试。
先用系统包管理器安装 tmux，然后在仓库目录执行：

```sh
git clone https://github.com/4ier/4top.git
cd 4top
python3 -m venv .venv
.venv/bin/python -m pip install ./packages/session-ls .
.venv/bin/4top --demo
. .venv/bin/activate
4top
```

必须同时提供两个本地包；安装不会假定 `session-ls 0.2.0` 已存在于注册表。
原始 agent CLI 需要单独安装、认证。4top 不主动添加权限绕过选项。
**历史恢复采用 CLI 当前的原生配置，不重放最初启动参数。**
例如，新建时临时添加的只读参数，不会成为 4top 永久保存的恢复策略；
恢复后发送任务前，应检查原生 CLI 的权限状态。

```sh
4top new codex
4top new claude --cwd ~/code/app --detach
4top search '中文' --full
4top list --json
4top doctor --json
```

## 日常操作

选中后按 Enter：已验证的 LIVE 行接入原进程；历史行经确认后恢复为新进程。
用你配置的 tmux prefix 后接 `d` 分离。在 tmux 外启动的面板会恢复原选择。
`q` 和 Ctrl-C 只关闭面板，不终止已完成交接的 agent。

`/` 搜索；Ctrl-F 明确触发全文搜索；Esc 清除或取消；`h` 展开历史；
空格只读预览；`i` 显示详情和危险操作；`n` 新建；`?` 查看帮助。
普通文字匹配支持中文和带引号的词组，多个词按 AND 匹配，不执行正则或历史内容。

在 tmux 内，4top 先恢复 TTY，再切换唯一能确认的调用 client，不嵌套、不踢掉
其他 client。面板在切换前退出；返回原调用 session 使用自己的 tmux 绑定。
同一个调用 session 有多个 client 时，CLI 必须给出精确 `--client`。
跨 socket 的嵌套接入被明确拒绝。

## 状态与恢复边界

Attach 接回相同进程；Resume 创建新进程，不能恢复消失的内存、后台子进程或网络连接。
`LIVE` 表示受管进程身份和 pane marker 已验证，不代表“正在思考”；退出码也不代表
任务成功。`HIST` 仅表示没有已确认的存活关联，外部启动的 agent 可能仍在别处运行。

新建 Codex 初始显示 unlinked，不使用“同目录 + 时间接近”自动绑定。
可通过 `4top link RUN HISTORY --yes` 人工关联。关联表示启动或人工确认的记录；
原生 `/new`、`/resume` 或 fork 后，4top 不声称持续知道当前上下文。

Claude Code、Codex、Pi 提供实验性启动和精确恢复驱动；Cursor 只读历史。
普通终端里已经启动的进程不能无损搬进 tmux。同目录多 agent 没有工作树隔离。
重复恢复保护只覆盖相同主机、用户和状态目录中的 4top 受管运行。

## 配置与隐私

可选配置位于 `~/.config/4top/config.toml`，支持 XDG 目录、`--config`、
`--socket`、`--no-color` / `NO_COLOR`。原生目录支持 `CODEX_HOME`、
`CLAUDE_CONFIG_DIR`、`PI_CODING_AGENT_DIR` 和显式配置覆盖。配置示例见英文 README。

元数据写入私有的 state/cache 目录。环境和原生命令参数通过一次性 Unix socket
在内存中交接，启动器最终 exec 原始 CLI；不写完整环境或 prompt 参数文件。
标题和路径本身可能敏感，录屏前仍需检查。诊断不自动上传。

`i` → Terminate 关闭精确验证的受管 pane，可能中断写入；优先接入后原生退出。
Dismiss 只隐藏已退出运行，不删除原生历史。启动握手和查询超时不限制 agent 总运行时长。

[完整操作与退出码](docs/troubleshooting.md) · [隐私](docs/privacy.md) ·
[贡献](CONTRIBUTING.md) · [兼容性](docs/compatibility.md)

MIT 许可。基于 4ier 的 session-ls，使用 tmux 与 Textual；不隶属于任何 agent 厂商。
