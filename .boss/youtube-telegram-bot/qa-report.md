# QA 测试报告

## 摘要

**最新质量结论：有条件通过。**

- **Gate 0（工程与安全启动门禁）：通过。** 当前代码可以在本机安全启动受控 Telegram polling，进入首次真实对话验收。未发现仍开放的 P0 代码缺陷。
- **Gate 1（真实交付门禁）：有条件通过，尚未关闭。** 真实 watch 720p 与带 `?si=` 的 `youtu.be` 分享链接 MP3 已下载、探测和首尾解码通过；真实 1080p、Shorts 以及 Telegram 实际收件下载回读仍需外部样例或用户先与 Bot 交互。

Backend 已关闭上一轮发现的并发 P0：每次链接提交使用随机唯一 task ID，请求只能被 claim 一次，同一 URL 的不同请求拥有独立缓存项和下载目录；清理不会跨任务；缓存增加 30 分钟 TTL、512 项容量限制和运行中保护。inspect 与 download 现共用 Deno/Node 等 yt-dlp JavaScript runtime 配置。新增回归后，本轮独立复跑结果为 **58 passed、2 个 live skipped**。

Telegram 收件尚未完成属于当前需要用户输入的外部门禁，不再误列为代码 P0；但在实际收件文件重新 ffprobe 和首尾解码前，仍不能宣称完整 Telegram E2E 已通过。

## 1. 验收范围与环境

- 工作区：`/Users/wsejoy/Documents/ChatGPT/telegram_bot`
- 评审基线：`prd.md`、`architecture.md`、`tech-review.md`、`tasks.md`
- Python：3.14.3（项目声明 `>=3.12`）
- python-telegram-bot：22.8
- yt-dlp：2026.08.19
- yt-dlp-ejs：0.8.0
- Deno：2.9.5；实际路径为项目虚拟环境内的 `.venv/bin/deno`
- Node：25.2.1，本机受 yt-dlp 支持
- ffmpeg / ffprobe：8.0.1
- pydantic-settings：2.15.0
- pytest：9.1.1
- pytest-asyncio：1.4.0

本轮复审只修改本报告，未修改业务代码、测试、依赖或本地 secret 配置。

## 2. Gate 0：工程与安全启动门禁

### 2.1 自动化回归

执行：

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/pytest -q -rs -p no:cacheprovider
```

结果：

```text
58 passed, 2 skipped
```

两个 skip 都是显式外部 live 门禁：

- `tests/live/test_telegram_e2e.py`：等待授权测试聊天与 Telegram 收件回读 harness。
- `tests/live/test_youtube_e2e.py`：本机未配置被忽略的真实样例文件 `tests/live/samples.json`。

它们没有被计作通过，也不影响判断代码是否可安全启动；它们继续约束 Gate 1 的最终结论。

### 2.2 编译、依赖和 Compose

- 使用临时 pycache 前缀执行 `python -m compileall -q src tests scripts`：退出码 0。
- `pip check`：`No broken requirements found`。
- `docker compose config --quiet`：退出码 0。
- 本轮没有执行 Docker 镜像构建或容器 live E2E；如果实际采用 Docker 部署，仍须在首轮部署中补验。

### 2.3 本机运行能力

`check_runtime_capabilities` 复跑结果：

```text
ready=True
yt-dlp=2026.8.19
yt-dlp-ejs=0.8.0
js_runtime=deno
issues=[]
```

ffmpeg、ffprobe、JavaScript runtime 和临时下载目录写入能力均通过。当前共享 runtime 枚举实际发现 Deno、Node 和 Bun；缺少的 runtime 不影响至少一个受支持 runtime 的启动门禁。

### 2.4 上一轮并发 P0 关闭复核

结论：**通过，P0-01 已关闭。**

代码和测试证据：

- `handlers.py:168-169` 使用 `_new_task_id` 为每次链接提交分配新 ID。
- `handlers.py:287-292` 使用 `secrets.token_urlsafe(9)`，并检查当前缓存中无碰撞。
- `DownloadRequest.claimed` 与 `handlers.py:200-209` 阻止同一按钮被重复执行；检查到设置之间没有 `await`，在单事件循环内不会被另一 callback 插入。
- 下载器继续使用 `downloads/<task_id>`，但 task ID 现在按请求唯一，因此同 URL 的不同用户不再共用可写目录。
- `handlers.py:251-256` 只移除当前 request ID 和当前任务目录。
- `test_same_url_messages_get_unique_isolated_task_ids` 验证相同 URL 两次提交得到不同且满足 Telegram callback 长度限制的 ID。
- `test_callback_cleanup_for_same_url_does_not_remove_other_request_or_directory` 验证一个同 URL 请求失败清理时，另一请求缓存和目录均保留。

### 2.5 缓存生命周期复核

结论：**通过。**

- 默认 `REQUEST_CACHE_TTL_SECONDS=1800`，`MAX_CACHED_REQUESTS=512`。
- `_prune_requests` 在链接入口、写入前后和 callback 入口执行。
- 未 claim 的过期项会删除；超容量时按创建时间移除最旧未 claim 项。
- 已 claim、正在运行的请求不会被 TTL 或容量清理，完成后由 callback `finally` 移除。
- `test_request_cache_prunes_expired_and_oldest_entries` 覆盖 TTL 与容量行为。

### 2.6 inspect/download runtime 一致性复核

结论：**通过。上一轮 Node/Deno 不一致风险已关闭。**

- `runtime.youtube_js_runtimes()` 统一生成 Deno、Node、QuickJS、Bun 配置。
- `media._extract_info` 与 `downloader.build_ytdlp_args` 均调用该共享函数。
- `test_extract_info_uses_shared_deno_and_node_runtime_options` 和 runtime 测试覆盖共享配置。
- 主 Agent 在修复后再次对 Google 官方视频执行真实 inspect，解析成功。

### 2.7 下载正确性与静态安全复核

以下离线门禁仍通过：

- 标准 watch、Shorts 与 `youtu.be?...` 规范化到单一 watch URL；移除 `list`、`index`、`t` 等参数。
- 纯播放列表和伪造 YouTube host 在下载前拒绝。
- 横屏和竖屏按 `min(width, height)` 归档清晰度。
- 1080p plan 由精确 format ID 组成，不包含低清 format 18 或 `height<=` 静默降级。
- 下载参数包含 `--no-playlist`、`--check-formats`、`--abort-on-unavailable-fragments`，不包含片段裁剪参数。
- 成品只接受唯一 JSON `after_move:RESULT`；路径越界、缺失、重复、临时件和空文件均拒绝。
- ffprobe 校验视频/音频流、时长容差和目标短边；兼容性转码和超限压缩后会再次探测。
- 未发现 `shell=True`、`create_subprocess_shell`、`os.system` 或运行时代码中的旧工程绝对路径。
- 未发现真实 Bot Token；secret 扫描命中仅为明显占位值和测试构造的假 Token。

### 2.8 Gate 0 结论

**通过。代码可安全启动受控 polling。**

“可安全启动”表示工程门禁、核心下载不变量、任务隔离、资源能力和已有真实本地产物均满足首次上线验证条件；不表示 Telegram 收件矩阵已经完成。

## 3. Gate 1：真实下载与 Telegram 收件门禁

### 3.1 真实 watch 720p

- 输入：Google 官方公开视频 `ylLzyHk54Z0` 的普通 watch 入口。
- 文件：`/tmp/telegram-bot-e2e.5EWN4B/google-api-overview-720/google-api-overview-720.mp4`
- 大小：24,586,481 bytes
- 时长：243.670204 秒
- 视频：1 流，H.264，1280×720
- 音频：1 流，AAC
- SHA-256：`9d3f85f817f388e2e4751c1c3c540f232281ed92879a48887ae632edc9173b0b`
- 首尾解码：开头 5 秒及从 238 秒至结尾均成功。
- 结论：**通过。**

### 3.2 真实 `youtu.be` 分享链接 MP3

- 实际输入：`https://youtu.be/ylLzyHk54Z0?si=telegram-bot-e2e`
- 规范化结果：绑定同一 video ID `ylLzyHk54Z0`，追踪参数没有进入实际下载 URL。
- 文件：`/tmp/telegram-bot-e2e.5EWN4B/google-api-overview-mp3/google-api-overview-mp3.mp3`
- 大小：4,671,920 bytes
- 时长：243.670204 秒
- 视频：0 流
- 音频：1 流，MP3
- SHA-256：`effe20fa2086849aa6a139bf76294a639d92027e6b8583cfab3a39a0f1dc3a0a`
- 首尾解码：开头 5 秒及从 238 秒至结尾均成功。
- 结论：**通过。分享链接参数已由真实下载覆盖，不再列为未测项。**

### 3.3 离线已覆盖、真实样例待补的项目

| 场景 | 离线证据 | 真实证据状态 |
|---|---|---|
| 纯播放列表拒绝 | URL 与 media 测试均覆盖 | 可在首轮验收做一次聊天提示确认 |
| 带 `list/t/index` 的 watch URL | 规范化测试覆盖移除参数 | 尚无独立真实下载产物 |
| Shorts URL 规范化 | URL 测试覆盖 | 缺真实竖屏 Shorts 下载与 Telegram 收件 |
| 竖屏短边分档 | media 与 ffprobe 测试覆盖 1080×1920 | 缺真实 Shorts 成品 |
| 缺片处理 | 下载参数测试确认 `--abort-on-unavailable-fragments` | 缺可控真实缺片样例；不应为制造样例而绕过访问控制 |
| after_move / 容器 / ffprobe | 路径、流、时长、清晰度与转码测试覆盖 | watch 720 与分享 MP3 已提供部分真实证据 |
| adaptive 1080p | 精确 selector 与错清晰度阻断测试覆盖 | 缺当前可授权下载的真实 1080p 样例 |

这些项目是 Gate 1 的真实环境验收缺口，不是当前已确认的代码缺陷。

### 3.4 Telegram 身份与收件状态

- 主 Agent 已用旧 Token 只读 `getMe`，确认 Bot 身份为 `@wsejoy_bot`；QA 未读取或输出 Token。
- 用户尚未先向该 Bot 发消息，因此目前没有可用于 polling 交互和收件回读的授权 chat/update。
- 尚未取得 Telegram message/file ID，也尚未从 Telegram 重新下载收件文件执行 ffprobe、首尾解码和清理核验。
- live Telegram 测试因此按设计 skip，而非失败。

结论：**外部门禁待用户交互。身份有效与本地文件正确不能替代 Telegram 实际收件。**

### 3.5 Gate 1 结论

**有条件通过，尚未最终关闭。**

已经具备普通 watch 720p 与真实 `youtu.be?...si=` MP3 的完整本地证据；尚缺真实 1080p、真实 Shorts 和 Telegram 实际收件回读。允许启动并进行首轮验收，不允许在这些证据完成前表述为“全矩阵生产验收通过”。

## 4. 残余非阻塞风险

### P1-01：阶段化错误仍未完全贯通

`delivery.send_download_result` 的 Telegram 上传异常仍可能被 handler 统一回落为 `DOWNLOAD_FAILED`；并发 limiter 的裸 `RuntimeError` 也没有稳定映射到 `LIMIT_REACHED`。这不会使已验证的媒体错误上传，但会降低用户提示和管理员日志的排障准确度。

建议后续把 upload、limit、merge、transcode、validation 边界统一转换为现有 `BotError` / `ErrorCode`，并补上传失败与限额分支测试。

### P1-02：依赖可重复性

`pyproject.toml` 仍使用 `>=` 下限，包括 `yt-dlp[default]>=2026.08.19`；当前环境已验证，但未来重新安装或 Docker 构建可能得到不同版本。建议增加 lock/constraints，至少固定生产构建依赖。

### P1-03：Docker 运行态尚未验证

Compose 静态检查通过，且共享 Deno runtime 代码已修复；但本轮未执行 Docker build、容器自检或容器真实下载。Docker 是可选部署方式，不阻塞本机 Gate 0；若选择 Docker 上线，应在部署前补做。

### P1-04：自动化覆盖与精确覆盖率

当前环境未安装 `pytest-cov`，因此本报告不声称精确行或分支覆盖率。60 项已收集测试中 58 项通过、2 项 live 跳过；模块覆盖广，但 Telegram 上传异常、完整 live harness、真实 1080p/Shorts 和部分管理员命令分支仍需补齐。

### P2-01：secret scanner 占位命中

`.env.example` 使用 `replace-with-your-bot-token` 占位符，不包含 Telegram Token 形状，可减少流水线误报。

## 5. 门禁汇总

| 门禁 | 结果 | 说明 |
|---|---|---|
| 离线回归 | 通过 | 58 passed，2 个外部 live skipped |
| Python 编译 | 通过 | `src tests scripts` compileall 退出 0 |
| 本机 runtime capabilities | 通过 | yt-dlp/EJS/Deno/ffmpeg/ffprobe/写目录 ready |
| `pip check` | 通过 | 无破损依赖 |
| Compose 静态配置 | 通过 | `docker compose config --quiet` 退出 0 |
| 同 URL 并发任务隔离 | 通过 | 随机唯一 ID、claim、防跨任务 cleanup 有测试 |
| 缓存 TTL / 512 容量 / 运行中保护 | 通过 | 实现与测试证据齐全 |
| inspect/download JS runtime 一致性 | 通过 | 共用 Deno/Node runtime，修复后真实 inspect 成功 |
| 列表拒绝、Shorts 规范化、缺片参数 | 通过（离线） | 对应自动化测试通过 |
| after_move、容器与 ffprobe | 通过（离线 + 部分真实） | 720p/MP3 真实产物补强 |
| 真实普通 watch 720p | 通过 | 双流、720、243.67 秒、首尾解码 |
| 真实 `youtu.be?...si=` MP3 | 通过 | 规范化正确、单音轨、243.67 秒、首尾解码 |
| 真实 adaptive 1080p | 待外部验收 | 缺授权且当前可用的真实样例 |
| 真实 Shorts | 待外部验收 | 缺授权竖屏样例 |
| Telegram 实际收件回读 | 待用户交互 | 用户需先向 `@wsejoy_bot` 发消息 |
| Docker build/runtime/live E2E | 可选部署待验收 | 不阻塞本机启动 |

## 6. 上线后首轮验收清单

Gate 0 已通过，可按以下顺序启动并关闭 Gate 1：

1. 用户先向 `@wsejoy_bot` 发送 `/start`，建立授权 chat/update。
2. 启动唯一 polling 实例，确认没有 409 多实例冲突，并实际验证 `/start`、`/help` 和格式按钮。
3. 发送已验证的 Google 官方视频，分别选择 720p 与 MP3；记录 Telegram 返回的 message/file ID。
4. 从 Telegram 下载实际收件文件到隔离临时目录，重新 ffprobe，并解码首尾；确认 720p 为双流 1280×720，MP3 为单音频流，时长约 243.67 秒。
5. 选择一个有权下载且当前提供 exact 1080p adaptive 的短视频，验证双流、短边 1080、时长完整且未命中 360p。
6. 选择一个有权下载的竖屏 Shorts，验证规范 video ID、方向、短边档位、双流和完整时长。
7. 向 Bot 发送纯播放列表 URL，确认下载前拒绝；发送带 `list/index/t` 的单视频 URL，确认只下载当前视频且从头开始。
8. 确认每次成功、失败和上传后只清理本任务目录；另一同 URL 请求和运行中任务不受影响。
9. 若采用 Docker，再执行镜像构建、容器 runtime 自检及至少 watch 720p、分享 MP3、1080p、Shorts 的容器下载验收。

完成第 1 至 8 项并保存非敏感证据后，可把 Gate 1 更新为完全通过；若采用 Docker，还应完成第 9 项后再批准容器部署。

## 7. 最终判定

**有条件通过：Gate 0 通过，代码可安全启动；Gate 1 等待真实 1080p、Shorts 和 Telegram 收件回读。**

当前没有开放的 P0 代码缺陷。剩余项目由真实样例可用性和用户首次 Telegram 交互驱动，应作为上线后首轮验收项持续跟踪，而不是误报为无用户输入即可修复的代码失败。
