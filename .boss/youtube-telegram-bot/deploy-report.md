# 部署报告

## 摘要

- **部署结论**：`@wsejoy_bot` 已改为 macOS LaunchAgent 常驻服务，当前状态为 `running`。
- **自动恢复**：已人工终止首次进程；`launchd` 在约 10 秒内自动拉起新进程，PID 从 `3845` 变为 `3867`。
- **断网恢复**：Telegram 长轮询持续重试；若进程意外退出，`KeepAlive` 会重新启动。网络恢复后无需手工操作。
- **开机行为**：用户登录 macOS 后通过 `RunAtLoad` 自动启动；不要求终端或 Codex 保持开启。
- **联网状态**：新进程已有 TCP 443 连接，Telegram `getMe` 验证为 `@wsejoy_bot`。

## 1. 部署结构

| 项目 | 路径或状态 |
|---|---|
| 源码工作区 | `/Users/wsejoy/Documents/ChatGPT/telegram_bot` |
| 后台运行副本 | `/Users/wsejoy/Library/Application Support/TelegramYTDLBot/app` |
| 独立虚拟环境 | `/Users/wsejoy/Library/Application Support/TelegramYTDLBot/.venv` |
| 私密配置 | `/Users/wsejoy/Library/Application Support/TelegramYTDLBot/.env`，权限 `600` |
| 启动脚本 | `/Users/wsejoy/Library/Application Support/TelegramYTDLBot/run_launchd_bot.zsh`，权限 `700` |
| LaunchAgent | `/Users/wsejoy/Library/LaunchAgents/com.wsejoy.telegram-ytdl-bot.plist` |
| 服务标签 | `com.wsejoy.telegram-ytdl-bot` |
| 日志目录 | `/Users/wsejoy/Library/Application Support/TelegramYTDLBot/logs` |

macOS 会限制后台进程直接读取 `Documents`，因此源码仍保留在工作区，后台服务使用部署到 `Application Support` 的独立运行副本。

## 2. 常驻与重连策略

- `RunAtLoad=true`：登录后自动启动。
- `KeepAlive=true`：进程退出后自动拉起。
- `ThrottleInterval=10`：限制异常重启频率。
- `ProcessType=Background`：作为后台进程运行。
- 旧配置的 `127.0.0.1:7890` 没有代理监听，运行副本已改用验证成功的 Telegram 直连。
- polling 在暂时断网时自行重试；进程若意外终止，`launchd` 提供第二层恢复。

## 3. 验证结果

```text
state = running
runs = 2
pid = 3867
properties = keepalive | runatload
telegram_ok = @wsejoy_bot
```

人工自动拉起测试：首次 PID `3845` 正常运行；发送 `SIGTERM` 后，约 10 秒自动恢复为 PID `3867`，stderr 日志为空。

- PID `3867` 存在已建立的 TCP 443 连接。
- 运行能力：yt-dlp `2026.08.19`、yt-dlp-ejs `0.8.0`、Deno `2.9.5`、ffmpeg/ffprobe 就绪。
- 自动化回归：`58 passed, 2 skipped`；两个 skip 是需用户参与的 live 门禁。
- Google 官方公开视频真实 720p 和 MP3 下载已通过完整性验证。
- 当前无开放的 P0 代码缺陷。

## 4. 运维命令

查看状态：

```bash
launchctl print gui/$(id -u)/com.wsejoy.telegram-ytdl-bot
```

重启：

```bash
launchctl kickstart -k gui/$(id -u)/com.wsejoy.telegram-ytdl-bot
```

查看错误日志：

```bash
tail -f "$HOME/Library/Application Support/TelegramYTDLBot/logs/launchd.err.log"
```

## 5. 剩余用户验收

常驻部署已完成。Gate 1 仍保留三项实际聊天验收：此前失败链接、真实 Shorts、当前确实提供 exact 1080p adaptive 流的视频。这些是外部样例验收，不是当前开放的代码缺陷。
