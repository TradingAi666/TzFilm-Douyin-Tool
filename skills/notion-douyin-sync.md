---
name: notion-douyin-sync
description: "抖音数据同步到 Notion：全量数据总览 + 最新作品追踪 + 粉丝数据，含字段映射和排错指南。"
version: 1.0.0
platforms: [macos]
---

# Notion 抖音数据同步

## 概述

每次 `douyin_hourly.py` 抓取抖音数据后，自动同步到 Notion：
- **视频数据总览**：仅最近 14 天发布的视频（仅公开视频），超期自动移入 Notion 回收站
- **最新作品追踪**：固定 key `"最新追踪"`，每次同步原地覆盖；旧追踪行自动移入回收站
- **账号总览**：只保留最新一条 total_fans + today_new_fans

## 关键文件

| 文件 | 用途 |
|---|---|
| `~/.codex/douyin-tool/scripts/notion_sync.py` | 核心同步脚本（三表：总览+追踪+账号） |
| `~/.codex/douyin-tool/scripts/douyin_hourly.py` | 抓取后调用 `notion_sync.py`，带 `fcntl` 并发锁 |
| `~/.codex/douyin-tool/scripts/douyin_new_video_tracker.py` | 追踪 checkpoint 后调用 `notion_sync.py` |

## Notion 凭证

在 Notion 创建 integration，复制 Internal Integration Token，然后把这个 integration 加到 3 个数据库所在页面。

```bash
# ~/.codex/douyin-tool/.env
NOTION_TOKEN=YOUR_NOTION_TOKEN
NOTION_SOURCE_OVERVIEW=YOUR_OVERVIEW_DATA_SOURCE_ID
NOTION_SOURCE_TRACKING=YOUR_TRACKING_DATA_SOURCE_ID
NOTION_SOURCE_ACCOUNT=YOUR_ACCOUNT_DATA_SOURCE_ID
```

兼容旧变量名：

```bash
NOTION_DATABASE_OVERVIEW=YOUR_OVERVIEW_DATABASE_ID
NOTION_DATABASE_TRACKING=YOUR_TRACKING_DATABASE_ID
NOTION_DATABASE_ACCOUNT=YOUR_ACCOUNT_DATABASE_ID
```

## Notion 字段结构

脚本会读取 Notion data source schema，并按列类型自动写入 `title`、`rich_text`、`number`、`date`、`select`、`status`、`checkbox`。

### 视频数据总览

默认 key 字段：`文本`

字段：文本, 发布时间, 播放量, 点赞, 评论, 分享, 收藏, 5s完播率(%), 完播率(%), 均时长(s), 互动率(%), 主页访问量, 粉丝增量, 均速/小时, 更新时间

### 最新作品追踪

默认 key 字段：`多行文本`

字段：多行文本, 检查时间, 已发布小时, 播放量, 增量, 点赞, 评论, 5s完播率(%), 互动率(%), 均时长(s), 预测播放, 预测等级, 置信度(%), 趋势

### 账号总览

默认 key 字段：`多行文本`

字段：多行文本, 总粉丝数, 今日新增

如果你的 key 列名不同：

```bash
NOTION_KEY_OVERVIEW=标题
NOTION_KEY_TRACKING=标题
NOTION_KEY_ACCOUNT=时间
```

## 数据过滤

- `公开`：同步到 Notion
- `自见`, `私密`, `未通过`, `审核中`, `已删除`：过滤掉
- 总览表只保留最近 14 天，SQL filter 使用 `date('now', 'localtime', '-14 days')`

## 故障排查

| 症状 | 检查 |
|---|---|
| Notion 返回 401 | `NOTION_TOKEN` 是否正确 |
| Notion 返回 404 | integration 是否已被邀请到数据库页面；data source ID 是否正确 |
| 某些字段没写入 | Notion 数据库是否真的有同名列；看终端 `Notion 缺少字段` 提示 |
| 新列不显示 | Notion API 不会自动建列，需要先在 Notion 数据库里创建列 |
| 仪表盘显示多行 | 手动跑一次 `python3 notion_sync.py`，旧追踪行会被移入回收站 |
| 今日新增始终为 0 | Douyin 首页无单日新增字段，脚本用 DB 差值计算；先确认 `account_stats` 是否有多条记录 |

## 关键设计约束

- Notion 删除动作使用 `in_trash: true`，数据不会硬删除。
- 总览表 14 天窗口 + 空标题/0 播放的幽灵行会自动清理。
- 追踪表单行模式固定 key `"最新追踪"`。
- 账号表清空旧行后只插入最新一条，适合仪表盘卡片。
- API 调用之间保留 0.06s 延迟，避免短时间打太多请求。
