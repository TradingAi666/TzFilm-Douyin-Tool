# 抖音创作者数据抓取 🎬

> 每小时自动从抖音创作者后台导出数据，零 API Key，纯 AppleScript 硬刚 Chrome。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/Platform-macOS-lightgrey)]()

## 为什么会有这个项目

我是抖音 AI/科技区的创作者。每天想看视频数据，就得手动打开 `creator.douyin.com` → 切「投稿列表」→ 点「导出数据」→ 下载 Excel → 自己分析。烦。

于是写了个脚本，让 AppleScript 操控 Chrome 自动化这套流程。结果发现——**这事比想象中恶心一百倍**。

## 踩坑笔记 🕳️

过程中踩了五个大坑，每个都卡了我好几天。全写进 `SKILL.md` 了，当反面教材看也行：

| # | 坑 | 一句话 | 谁可能遇到 |
|---|---|---|---|
| 1 | **TCC 沙箱** | launchd 进程读不了 `~/Downloads`，哪怕加了 Full Disk Access 也没用 | macOS 上跑定时任务的 |
| 2 | **Chrome 后台 tab 静默吞下载** | AppleScript 注入的 `click()` 不算 user gesture，下载直接被 Chrome 拦截 | 做浏览器自动化的 |
| 3 | **AppleScript 里 Unicode 转义全废** | `\u5bfc\u51fa` 写进 AppleScript 静默失败，必须用 `charCodeAt()` | 处理中文 UI 的 |
| 4 | **Garfish 微前端 DOM 隔离** | 字节的 Garfish 框架让 `body.innerText` 只返回 460 个字符的导航栏 | 逆向字节系产品的 |
| 5 | **launchd 不设 HOME** | `os.path.expanduser("~")` 在后台返回错误路径 | 写 macOS launchd 的 |

### 怎么绕过的？

- **TCC 沙箱**：`launchd Python → osascript → do shell script → bash → mv`，利用 osascript 继承 GUI session 的 TCC 上下文
- **后台 Tab 下载**：三行 AppleScript：`activate` + `set active tab index` + `set index of w to 1`，强制把 Chrome 拉到前台
- **Unicode 匹配**：放弃 `\u`，用 `"导出数据".charCodeAt(0) === 23548` 硬匹配

## 它能做什么

```
抖音创作者中心 (creator.douyin.com)
    │
    │ AppleScript 操控 Chrome
    ▼
[切换「投稿列表」tab]
    │
    │ 点击「导出数据」
    ▼
[下载 作品列表.xlsx]
    │
    │ openpyxl 解析
    ▼
[SQLite 数据库]
    │
    │ 可选：Telegram Bot 推送
    ▼
📊 每次运行都有最新的 100 条视频数据
```

- ✅ 抓取全部视频的播放/点赞/评论/分享/收藏/完播率/CTR
- ✅ 历史快照保留，可追踪增长
- ✅ launchd 每小时自动跑，完全无人值守
- ✅ 纯净 — 零 API Key，不需要任何第三方服务
- 💬 **新！智能评论自动回复** — 导出评论 → AI 生成回复 → Playwright 自动发送

## 5 分钟上手

### 前置条件

- 🍎 macOS
- 🌐 Google Chrome
- 🔑 已登录 [creator.douyin.com](https://creator.douyin.com)
- ⚙️ Chrome 菜单 → **显示** → **开发者** → 勾选 ✅ **允许 Apple 事件中的 JavaScript**

### 安装

```bash
# 1. 装依赖
pip install openpyxl

# 2. 克隆
git clone https://github.com/TradingAi666/TzFilm-Douyin-Tool.git
cd TzFilm-Douyin-Tool
```

### 跑一次试试

```bash
python3 douyin_hourly.py
```

第一次跑，macOS 可能会弹窗问「允许 Terminal 控制 Google Chrome」→ 点**好**。

### 设为定时任务

```bash
# 编辑 plist，把 YOUR_USERNAME 换成你的 macOS 用户名
# 然后：
cp com.codex.douyin-tracker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.codex.douyin-tracker.plist
```

搞定。以后每小时自动跑一次。

### 配合 Codex Agent 用

```bash
# 把 SKILL.md 放到 Codex skills 目录
mkdir -p ~/.codex/skills/douyin
cp SKILL.md ~/.codex/skills/douyin/TzFilm-Douyin-Tool.md
```

然后跟 Codex 说一句：

> @Codex 拉取抖音数据

Agent 会自动按 SKILL.md 里的流程操作。

### 想推送到 Telegram？

在 `~/.codex/douyin-tool/.env` 里加两行：

```
TELEGRAM_BOT_TOKEN=你的Bot Token
TELEGRAM_HOME_CHANNEL=你的Chat ID
```

### 想同步到 Notion？

先在 Notion 创建 3 个数据库 / data source，并把集成授权给这些页面：

```bash
# ~/.codex/douyin-tool/.env
NOTION_TOKEN=你的Notion集成Token
NOTION_SOURCE_OVERVIEW=视频数据总览的data_source_id
NOTION_SOURCE_TRACKING=最新作品追踪的data_source_id
NOTION_SOURCE_ACCOUNT=账号总览的data_source_id
```

字段名默认沿用脚本里的中文列名：总览表 key 是 `文本`，追踪和账号表 key 是 `多行文本`。如果你的 Notion key 列不同，可以加：

```bash
NOTION_KEY_OVERVIEW=标题
NOTION_KEY_TRACKING=标题
NOTION_KEY_ACCOUNT=时间
```

## 文件清单

| 文件 | 说明 |
|---|---|
| `douyin_hourly.py` | 🔧 核心抓取脚本（~500 行 Python + 内嵌 AppleScript） |
| `douyin_new_video_tracker.py` | 🔮 新视频追踪+预测（每30分钟采集 → 同期对比 → 预测最终播放） |
| `prediction_query.py` | 📊 查询追踪数据：最新快照 / 增长曲线 / 24h倍率 |
| `notion_sync.py` | 🔗 同步抖音数据到 Notion（三表：总览 / 追踪 / 账号） |
| `auto_reply.py` | 💬 Python 一键封装：`export` 导出评论 / `reply` 批量回复 |
| `auto-reply/` | 🎭 Node.js + Playwright 浏览器自动化（评论抓取 & 发送） |
| `schema.sql` | 🗃️ 完整数据库结构（含 video_tracking 预测表） |
| `SKILL.md` | 📖 AI Agent 操作手册（含全部踩坑记录和正确的做法） |
| `com.codex.douyin-tracker.plist` | ⚙️ launchd 配置模板 |
| `README.md` | 📄 你正在看的东西 |

---

## 🔮 新功能：视频播放预测

上传新视频后，可以用 `douyin_new_video_tracker.py` 启动追踪：

```bash
# 初始化追踪（记录基线 + 首次预测）
python3 douyin_new_video_tracker.py --init --title "你的视频标题"

# 之后每30分钟跑一次，自动对比历史同期视频预测最终播放量
python3 douyin_new_video_tracker.py --title "你的视频标题"
```

### 预测原理（v3 同期对比模型）

```
预测最终 = 当前播放 × 同期倍率中位数 × CTR修正 × 互动修正 × 均时长修正
```

1. **同期倍率**：从你的历史视频中，找到同一发布时长（如 2.3h）时的播放量，计算它们到最终播放的倍率中位数
2. **三维质量修正**：CTR / 互动率 / 均时长 分别与同期视频做百分位对比 → 优秀者获得加成，落后则打折
3. **置信度**：随追踪时间增长（<2h→25%，≥24h→90%），初期粗、后期准

### 查询追踪数据

```bash
# 所有追踪视频的最新状态
python3 prediction_query.py latest

# 完整增长曲线
python3 prediction_query.py growth

# 24h→最终倍率（模型校准）
python3 prediction_query.py ratios

# 指定视频的完整记录
python3 prediction_query.py track "视频标题"
```

### 配 Codex Agent 用

把追踪脚本加入 cron，每 30 分钟自动更新：

```bash
# Codex 里一句搞定：
@codex 追踪新视频 --init --title "xxx"
# 然后自动生成 48 次 cron（24h × 每30min）
```

## 数据库结构

完整 schema 见 [`schema.sql`](schema.sql)。两个模块共用同一数据库：

| 表 | 写入方 | 用途 |
|---|---|---|
| `video_stats` | `douyin_hourly.py` | 每小时原始数据快照（为预测模型提供历史同期数据） |
| `video_tracking` | `douyin_new_video_tracker.py` | 每30分钟检查点（含预测结果） |
| `video_tracking_meta` | `douyin_new_video_tracker.py` | 追踪元信息（基线、活跃状态） |

关键查询示例见 `schema.sql` 末尾。

---

## 💬 新功能：评论自动回复

不想手动一条条回复评论？这套工具可以自动搞定：**导出评论 → AI 生成回复 → Playwright 模拟人工逐条发送**。

### 首次安装

```bash
# 安装 Node.js 依赖 + Playwright 浏览器 + 登录
python3 auto_reply.py setup
```

> 运行后会弹出 Chrome，请手动登录抖音创作者后台，回终端按回车保存。

### 导出评论

```bash
# 扫描指定视频的全部评论
python3 auto_reply.py export "视频标题关键词"
```

导出结果保存在 `auto-reply/comments-output/unreplied-comments.json`。

### AI 生成回复文案

你需要用任意 AI 工具（ChatGPT / Claude / Codex 等）为这些评论生成回复，保存为：

```
auto-reply/comments-output/auto-reply-plan.json
```

格式：
```json
{
  "selectedWork": {"title": "...", "publishText": "..."},
  "comments": [
    {
      "username": "用户A",
      "commentText": "原评论内容",
      "replyMessage": "AI生成的回复"
    }
  ]
}
```

### 批量发送回复

```bash
# 试运行预览（不真正发送）
python3 auto_reply.py reply "视频标题关键词" --dry-run

# 正式发送（每批30条）
python3 auto_reply.py reply "视频标题关键词"
```

脚本自动打开 Playwright 浏览器，逐条匹配评论 → 点击回复 → 输入文案 → 发送。支持跨平台（macOS / Windows / Linux），不依赖 AppleScript。

---

## 注意事项

- 🔴 抓取时会抢占 Chrome 焦点约 30 秒（硬伤，除非改用 CDP）
- 🟡 抖音创作者中心的 DOM 结构偶尔变动，如果抓取失败，可能是 Garfish 容器 ID 变了（SKILL.md 里有调试方法）
- 🟢 运行频率建议 ≥ 1 小时一次，太频繁可能触发风控
- 🟢 抖音后台最多导出最新的 ~100 条视频，旧视频的历史数据从快照中恢复

## License

MIT — 随便用，欢迎 PR。

---

⭐ 如果对你也有用，给个 Star？有什么问题直接开 Issue，或者群里找我。
