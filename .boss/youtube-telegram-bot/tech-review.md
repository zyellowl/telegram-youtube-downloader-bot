---
type: tech-review
outputFor: [scrum-master, developer, qa, devops]
dependencies: [prd, architecture]
---

# 技术评审报告

## 文档信息

- **功能名称**：youtube-telegram-bot
- **创建日期**：2026-08-23
- **评审对象**：`prd.md` v1.1、`architecture.md` v1.1、旧项目 `src/` 与 `tests/`
- **评审结论**：**有条件批准架构，阻塞直接迁移上线**
- **最高优先级**：P0，先修复真实 YouTube 下载准确性和完整性，再做 Telegram 交付验收

## 1. 结论摘要

PRD 的产品边界正确，架构选择也基本合理：继续使用单进程异步 Bot、`YoutubeDL` 做检查、yt-dlp 子进程做下载、ffmpeg/ffprobe 做媒体处理，不需要重写框架或引入数据库。但是，旧实现不能原样迁移后宣称可用。

主线程已经用真实链接复现：选择 1080p 时，旧 selector 实际命中 YouTube format `18`，得到 360p 合并流。旧代码还缺少完整 YouTube JavaScript 支持、默认允许跳过不可用分片、按“最新修改文件”猜成品，并且只以进程返回码和文件大小判断成功。这些问题能直接解释用户所说的“链接对应不准、不是失败就是少”。

因此技术结论是：

1. `architecture.md` 提出的 `SelectionPlan`、`after_move` 和 ffprobe 后置条件应作为强制实现，不是可选增强。
2. 必须补上 `--check-formats`、`--abort-on-unavailable-fragments` 和 `yt-dlp[default]`/EJS 运行能力；当前架构文字尚未把前两个参数明确列为下载命令不变量。
3. 在真实 720p、1080p、Shorts、分享链接和 MP3 全链路通过前，项目状态不得标记为“完成”或“可交付”。旧有 `32 passed` 只是回归基线。
4. 最小修复限于选流、依赖、成品定位、媒体校验、相关清理和测试；不增加 Web UI、数据库、登录绕过、多平台或公网部署。

## 2. 已确认的旧代码问题

| 严重度 | 证据 | 影响 | 评审结论 |
|---|---|---|---|
| P0 | `downloader.py:41-46` 的 selector 为 `best[height<=H][ext=mp4]/bestvideo...+bestaudio...` | `/` 是按顺序回退。只要 360p progressive MP4 可用，720p/1080p 请求就先命中它；真实复现为 1080p 请求选到 format 18/360p | 删除 progressive-first selector，检查和下载必须共用同一精确计划 |
| P0 | `test_downloader.py:47-56` 明确断言 progressive MP4 必须优先 | 单元测试把错误行为固化成“正确”，所以测试全绿无法发现真实降级 | 删除/反转该断言，新增“1080p 不得选 360p”的回归测试 |
| P0 | `media.py:88-105` 只接受精确 `height` 360/480/720/1080，并只按 MP4 与大小评分 | 竖屏 1080×1920 Shorts 会被归到错误档或漏掉；未区分 video-only、audio-only、codec 和协议 | 以 `min(width, height)` 表达用户看到的清晰度，并生成包含音视频组合的 `SelectionPlan` |
| P0 | `MediaChoice.format_id` 在 `media.py` 中被保存，但 `handlers.py:211-218` 和 `downloader.py` 下载时完全不使用 | UI 显示的格式与真实下载没有契约，第二次选择可漂移 | 回调只携带服务端 `plan_id`；下载只接受缓存的计划，不接受客户端 selector |
| P0 | yt-dlp 默认 `--skip-unavailable-fragments`；旧命令未覆盖该默认值 | 某些 DASH/HLS 分片不可用时，命令可能仍成功并产出缺段文件 | 下载命令强制 `--abort-on-unavailable-fragments`，任何缺片都失败且不上传 |
| P0 | 旧命令没有 `--check-formats` | 元数据中存在但当前不可下载的格式可能在真正下载阶段失败或触发不合预期回退 | 对最终计划启用 `--check-formats`；候选不可下载时返回“格式不可用”，不得降档冒充成功 |
| P0 | `downloader.py:86-88,101-105` 在任务目录中返回最新修改的文件 | 可能选中 `.part`、分片、sidecar、中间媒体或错误的后处理产物 | 仅接受 yt-dlp 的 `after_move:filepath` 机器标记，并做目录边界和文件类型验证 |
| P0 | `transcode.py:45-62` 只探测压缩前的 format duration；普通下载没有流、分辨率、时长校验 | 无声视频、仅音频、错清晰度、截断文件都会被当作成功上传 | 所有最终产物在上传前统一执行 ffprobe JSON 校验；转码/压缩后再次校验 |
| P0 | `pyproject.toml:13` 仅声明 `yt-dlp>=2025.0`，没有 default extra；旧环境复现时 yt-dlp 已过期，当前旧 venv虽显示 `2026.03.17`，但 `yt_dlp_ejs` 仍不可导入；Dockerfile 只安装 ffmpeg | 不同安装时间和本机/容器会得到不同 YouTube 格式能力，缺 EJS 时格式清单可能不完整 | 目标项目使用经真实验收的锁定版本 `yt-dlp[default]`，显式提供受支持 JS runtime/EJS，并做启动自检 |
| P1 | `handlers.py:228-230` 只有 `result is not None` 才按结果路径清理 | 下载、after_move 解析或 ffprobe 校验失败时，部分文件可能残留 | `finally` 应按已知 `task_id/task_dir` 清理，不依赖成功结果对象 |
| P1 | 现有 media/downloader/transcode 测试全部是构造参数或 mock | 未覆盖真实格式清单、真实音视频合并、分片缺失和 Telegram 收件成品 | 保留离线测试，但新增受控真实 E2E 发布门禁 |

## 3. PRD 与架构评审

### 3.1 批准项

- 产品目标明确把真实下载准确性放在 P0，并要求 Telegram 实际收到的文件作为最终验收对象。
- 旧目录只读、目标目录独立迁移、secret 不复制不输出的边界正确。
- 保留现有单体模块边界是最小风险方案；本轮不需要数据库、Redis、Web API 或微服务。
- `SelectionPlan`、规范单视频 URL、任务独立目录、`after_move`、ffprobe、失败分阶段和真实 E2E 的总体方向正确。
- 720p/1080p 独立音视频流、MP3、Shorts 和分享链接均被列为发布门禁，覆盖了用户已报告的主要失败模式。

### 3.2 必须修正或澄清项

1. **清晰度验收术语存在冲突。** PRD 多处写“实际视频高度等于所选清晰度”，但架构又正确规定横竖屏统一按画面短边。对于 1080×1920 Shorts，`height` 是 1920，用户选择的 1080p 对应 `width`/短边 1080。开发和 QA 应统一为：`min(width, height) == target_resolution`；同时在报告中记录实际 width 与 height。横屏才可直接断言 height。
2. **禁止模糊降级。** 架构中“目标以内最佳”和 `res:H` 可作为候选排序思路，但不能单独构成验收语义，否则 1080p 请求仍可能得到 720p。按钮只在存在目标短边的可执行计划时展示；点击后该计划失效就明确失败并让用户重新解析。
3. **删除受限扫描兜底。** `architecture.md:590` 允许 `after_move` 缺失时扫描目录兜底，这会重新引入本次已确认的错文件风险。MVP 应把缺失、重复或非法 `RESULT` 标记视为下载协议失败；不要静默扫描猜测。
4. **依赖状态不能写“声明已就绪”。** 当前声明没有保证经验证的 yt-dlp 版本、default extra、EJS 或容器 JS runtime。完成依赖锁定和两种环境能力检查前，该项仍是阻塞状态。

## 4. P0 阻塞项与最小实现约束

### P0-1：检查与下载共享精确 SelectionPlan

`SelectionPlan` 至少应保存：规范 video ID/URL、目标短边、首选和同档候选 format ID、是否要求音轨、预期媒体类型、源时长以及计划过期时间。

视频计划的候选只能来自同一规范视频、同一目标短边：

- 首选：同档 video-only MP4 + audio-only M4A；
- 第二选择：同档通用 video-only + audio-only，由 ffmpeg 合并，必要时再转 H.264/AAC MP4；
- 最后选择：同档且同时包含音视频的 combined 格式；
- 不得把 360p/480p/720p 放进 1080p 计划，也不得用 `best[height<=H]` 作为静默兜底。

下载 selector 应由检查结果中的明确 format ID 组成，例如：

```text
<preferred_video_id>+<preferred_audio_id>/<same_resolution_alt_video_id>+<alt_audio_id>/<same_resolution_combined_id>
```

所有 `/` 后备项都必须满足同一目标短边。若没有同档后备，就只使用首选 `<video_id>+<audio_id>` 并失败退出。不要继续使用：

```text
best[height<=H][ext=mp4]/bestvideo[height<=H][ext=mp4]+bestaudio[ext=m4a]/best[height<=H]
```

MP3 可由 yt-dlp 选择最佳可用音频后提取，但仍必须绑定同一规范 video ID，并通过最终音轨与时长校验。

### P0-2：完整 YouTube 运行依赖

- 目标环境将 `yt-dlp` 改为经本轮真实 E2E 验证并锁定的版本，安装形态为 `yt-dlp[default]`，不能继续使用无上限的 `yt-dlp>=2025.0` 作为唯一可重复性保证。
- 验证 `yt_dlp_ejs` 实际可导入，并提供 yt-dlp 支持的 JavaScript runtime；本机和 Docker 都必须显式满足，不能依赖开发者电脑“碰巧装过”。
- 启动自检应输出版本/能力是否就绪，不输出 Token、代理凭据或 `.env` 内容；缺少 yt-dlp、EJS、JS runtime、ffmpeg、ffprobe 时快速失败。
- 不再用 `no_warnings=True` 吞掉会影响格式完整性的 yt-dlp 警告；警告写脱敏管理员日志。

### P0-3：下载参数必须拒绝不可用格式和缺失分片

视频与音频任务的固定下载参数至少包含：

```text
--no-playlist
--check-formats
--abort-on-unavailable-fragments
--retries 5
--fragment-retries 5
```

`--check-formats` 负责在选定格式实际不可下载时中止；`--abort-on-unavailable-fragments` 覆盖 yt-dlp 默认的 `--skip-unavailable-fragments`，防止缺段成品被当作成功。重试耗尽后必须进入 `DOWNLOAD_FAILED`，不得上传已有部分文件。

### P0-4：after_move 是唯一成品路径协议

下载命令应同时保留显式进度并打印后处理后的路径，例如：

```text
--progress
--newline
--progress-template download:PROGRESS:%(progress._percent_str)s %(progress._speed_str)s ETA %(progress._eta_str)s
--print after_move:RESULT:%(filepath)j
```

`--print` 会影响 quiet/progress 行为，因此必须显式 `--progress`。解析器只接受一个 `RESULT:`，对 JSON 转义路径解码，并验证：

- `resolve()` 后仍位于当前任务目录；
- 是存在的普通文件且大小大于 0；
- 不是 `.part`、`.ytdl`、sidecar、分片或临时文件；
- MP3 后处理、音视频合并或转码已经结束。

缺少、重复、无法解析或越界的结果路径都应判为 `DOWNLOAD_FAILED`，不得退回 `_newest_file()`。

### P0-5：上传前 ffprobe 后置条件

统一使用类似以下参数读取结构化结果：

```text
ffprobe -v error -show_format -show_streams -of json <final_path>
```

验证规则：

- 音频任务：至少一个可识别音频流，不得包含意外视频流；成品时长与源时长差不超过 `max(2 秒, 源时长 × 1%)`。
- 视频任务：至少一个视频流和一个音频流；`min(width, height)` 必须严格等于计划目标；时长满足同一容差。
- 文件必须非空、容器可读；准备使用 `sendVideo` 时应满足 Telegram 兼容容器/codec，否则先转为 H.264/AAC MP4。
- yt-dlp 合并后、兼容性转码后、超限压缩后都要校验最终待上传文件，不能只校验中间源文件。
- ffprobe 非零、JSON 不完整、缺流、错清晰度或时长超差均进入 `VALIDATION_FAILED`，文件不得上传。

实时 ffprobe 负责低成本门禁；真实 E2E 还应检查首尾可解码/可播放，以覆盖仅靠容器时长无法发现的媒体损坏。

### P0-6：真实 E2E 发布门禁

自动单元/集成测试通过后，仍必须在目标工作区完成真实样例矩阵：

| 场景 | 必须确认的证据 |
|---|---|
| 普通 watch URL | 规范 video ID、标题、完整时长一致 |
| Shorts（竖屏） | 正确 video ID；短边清晰度正确；完整时长 |
| `youtu.be`/官方分享链接，带跟踪、时间戳或 `list=` 参数 | 只下载当前单视频；不从时间戳截断；不误下播放列表 |
| adaptive-only 720p | Telegram 收件含视频流和音频流；短边 720；时长合格 |
| adaptive-only 1080p | Telegram 收件含视频流和音频流；短边 1080；不得命中 format 18/360p |
| MP3 | Telegram 收件为完整可播放音频；标题与时长合格 |
| 缺片或选定格式不可下载 | 明确失败；不发送残缺文件；任务目录和并发名额被释放 |

最终判定对象必须是 Telegram 实际收到并重新保存的文件，而不是下载目录中的中间产物。每条记录保存非敏感证据：任务 ID、规范 video ID、目标档、实际 format ID、width/height、音视频流数量、源/成品时长、文件大小、发送方式和清理结果。任何必测项失败即阻止发布。

## 5. 最小改动范围

| 模块 | 必要改动 | 不在本轮范围 |
|---|---|---|
| `models.py` | 增加不可变 `SelectionPlan`/校验结果；`MediaChoice` 引用 `plan_id` | 数据库模型、持久任务历史 |
| `media.py` | 规范单视频 ID/URL；拒绝 playlist-only；按短边与流类型生成同档计划 | 登录、Cookie、地区/DRM 绕过 |
| `handlers.py` | 服务端 plan lookup；禁止篡改；阶段化错误；按 task dir 清理 | 新 UI、用户系统 |
| `downloader.py` | 精确 ID selector；`--check-formats`；`--abort-on-unavailable-fragments`；after_move 协议 | 下载其他网站、复杂队列 |
| `transcode.py` | ffprobe JSON 解析与媒体不变量；兼容性转码/压缩后复验 | 重做全部转码策略 |
| `pyproject.toml`/Docker | 锁定 `yt-dlp[default]`；显式 JS runtime/EJS；ffmpeg/ffprobe 自检 | 公网部署、Local Bot API 强制启用 |
| `tests/` | 替换错误 selector 断言；增加竖屏、计划一致性、缺片、after_move、路径越界、缺流/错分辨率/时长超差测试 | 用 mock E2E 冒充真实验收 |
| QA 清单 | 固定授权公开样例并验证 Telegram 收件 | 保存私密链接、Token 或敏感日志 |

## 6. 开发顺序与验收门槛

1. 只读复制允许迁移的旧源码与测试到目标目录，记录旧目录关键文件校验；不迁移 `.env`、`.venv`、下载产物和缓存。
2. 先增加能稳定复现“1080p 计划不得选 format 18/360p”的失败测试，并删除旧 progressive-first 预期。
3. 实现 `SelectionPlan` 和服务端 plan lookup，再修改 downloader；不要让 UI 与下载各自计算格式。
4. 补齐 `yt-dlp[default]`、EJS、JS runtime、`--check-formats`、`--abort-on-unavailable-fragments` 和 after_move 协议。
5. 实现 ffprobe 后置校验与失败清理；对转码/压缩产物复验。
6. 运行原有不少于 32 项回归、新增单元/集成测试和 `compileall`。
7. 在无 Token 输出的前提下启动唯一 polling 实例，执行真实下载矩阵与 Telegram 收件验证。
8. 只有全部 P0 证据通过后，才更新 QA/部署报告为可交付。

## 7. 技术完成定义

- [ ] 1080p 真实复现不再选择 format 18/360p，720p/1080p 均严格匹配目标短边且包含音视频流。
- [ ] 目标环境使用经验证的 `yt-dlp[default]` 锁定版本，EJS、JS runtime、ffmpeg、ffprobe 启动检查通过。
- [ ] 所有下载均启用 `--check-formats` 和 `--abort-on-unavailable-fragments`。
- [ ] `_newest_file()` 不再参与成功路径；after_move 缺失或非法时任务失败。
- [ ] 每个上传文件均通过流、时长、分辨率、容器的 ffprobe 校验；转码后复验。
- [ ] 错 selector、缺音轨、缺分片、错时长、路径越界和校验失败均有自动回归且不上传文件。
- [ ] 普通 URL、Shorts、分享链接、adaptive 720p、adaptive 1080p、MP3 全部通过真实 Telegram 收件 E2E。
- [ ] 成功和所有失败路径都释放限额并清理任务目录；旧项目和 secret 未被修改、读取、输出或提交。

在以上项目全部关闭前，Tech Lead 不批准发布；关闭后无需再次进行架构重写，可直接进入 QA 最终验收。
