#!/usr/bin/env python3
"""抖音数据 -> Notion 同步脚本

- 数据总览: 最近 14 天公开视频的最新快照，Notion 中超期/空行自动移入回收站
- 最新作品追踪: 固定 key「最新追踪」，每次原地覆盖
- 账号总览: 只保留最新一条账号粉丝数据
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime

ENV_PATH = os.path.expanduser("~/.codex/douyin-tool/.env")
NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
DB_PATH = os.environ.get("DOUYIN_DB_PATH") or os.path.expanduser("~/.codex/douyin-tool/douyin_stats.db")


def _load_env_var(key):
    if key in os.environ:
        return os.environ[key]
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


NOTION_TOKEN = _load_env_var("NOTION_TOKEN") or "YOUR_NOTION_TOKEN"
SOURCE_OVERVIEW = _load_env_var("NOTION_SOURCE_OVERVIEW") or _load_env_var("NOTION_DATABASE_OVERVIEW") or "YOUR_NOTION_OVERVIEW_SOURCE_ID"
SOURCE_TRACKING = _load_env_var("NOTION_SOURCE_TRACKING") or _load_env_var("NOTION_DATABASE_TRACKING") or "YOUR_NOTION_TRACKING_SOURCE_ID"
SOURCE_ACCOUNT = _load_env_var("NOTION_SOURCE_ACCOUNT") or _load_env_var("NOTION_DATABASE_ACCOUNT") or "YOUR_NOTION_ACCOUNT_SOURCE_ID"

KEY_OVERVIEW = _load_env_var("NOTION_KEY_OVERVIEW") or "文本"
KEY_TRACKING = _load_env_var("NOTION_KEY_TRACKING") or "多行文本"
KEY_ACCOUNT = _load_env_var("NOTION_KEY_ACCOUNT") or "多行文本"

_schema_cache = {}


def notion_api(method, path, data=None):
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request(NOTION_BASE_URL + path, data=body, method=method)
    req.add_header("Authorization", f"Bearer {NOTION_TOKEN}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {"message": str(e)}
        payload["status"] = e.code
        return payload


def get_schema(source_id):
    if source_id not in _schema_cache:
        resp = notion_api("GET", f"/data_sources/{source_id}")
        if resp.get("object") != "data_source":
            raise RuntimeError(f"Notion data source 读取失败: {resp}")
        _schema_cache[source_id] = resp.get("properties", {})
    return _schema_cache[source_id]


def _plain_text(prop):
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in prop.get(ptype, []))
    if ptype == "number":
        value = prop.get("number")
        return "" if value is None else str(value)
    if ptype == "date":
        value = prop.get("date") or {}
        return value.get("start") or ""
    if ptype in ("select", "status"):
        value = prop.get(ptype) or {}
        return value.get("name", "")
    if ptype == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    return str(prop.get(ptype, "") or "")


def _to_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S") if " " in fmt else dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return text.replace(" ", "T")


def _notion_value(prop_schema, value):
    ptype = prop_schema.get("type")
    if ptype == "title":
        text = str(value or " ").strip() or " "
        return {"title": [{"text": {"content": text[:2000]}}]}
    if ptype == "rich_text":
        text = str(value or "").strip()
        return {"rich_text": [{"text": {"content": text[:2000]}}]} if text else {"rich_text": []}
    if ptype == "number":
        try:
            return {"number": float(value or 0)}
        except (TypeError, ValueError):
            return {"number": 0}
    if ptype == "date":
        date_value = _to_date(value)
        return {"date": {"start": date_value} if date_value else None}
    if ptype == "select":
        text = str(value or "").strip()
        return {"select": {"name": text[:100]} if text else None}
    if ptype == "status":
        text = str(value or "").strip()
        return {"status": {"name": text[:100]} if text else None}
    if ptype == "checkbox":
        return {"checkbox": bool(value)}
    return None


def build_properties(source_id, fields):
    schema = get_schema(source_id)
    props = {}
    missing = []
    unsupported = []

    for name, value in fields.items():
        prop_schema = schema.get(name)
        if not prop_schema:
            missing.append(name)
            continue
        notion_value = _notion_value(prop_schema, value)
        if notion_value is None:
            unsupported.append(name)
            continue
        props[name] = notion_value

    if missing:
        print(f"  ⚠️ Notion 缺少字段: {', '.join(missing)}")
    if unsupported:
        print(f"  ⚠️ Notion 字段类型暂未支持: {', '.join(unsupported)}")
    return props


def query_pages(source_id, page_size=100):
    pages = []
    cursor = None
    while True:
        payload = {"page_size": page_size}
        if cursor:
            payload["start_cursor"] = cursor
        resp = notion_api("POST", f"/data_sources/{source_id}/query", payload)
        if resp.get("object") != "list":
            raise RuntimeError(f"Notion 查询失败: {resp}")
        pages.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
        time.sleep(0.2)
    return pages


def fetch_key_map(source_id, key_field):
    mapping = {}
    for page in query_pages(source_id):
        key = _plain_text(page.get("properties", {}).get(key_field)).strip()
        if key:
            mapping[key] = page["id"]
    return mapping


def trash_page(page_id):
    resp = notion_api("PATCH", f"/pages/{page_id}", {"in_trash": True})
    return resp.get("object") == "page"


def upsert_page(source_id, key_field, key, fields, existing_map):
    props = build_properties(source_id, fields)
    if not props:
        return False, "skip"

    if key in existing_map:
        page_id = existing_map[key]
        resp = notion_api("PATCH", f"/pages/{page_id}", {"properties": props})
        return resp.get("object") == "page", "update"

    resp = notion_api("POST", "/pages", {
        "parent": {"type": "data_source_id", "data_source_id": source_id},
        "properties": props,
    })
    if resp.get("object") == "page":
        existing_map[key] = resp["id"]
        return True, "create"
    print(f"  ⚠️ Notion 创建失败: {resp}")
    return False, "create"


def sync_overview():
    """同步「视频数据总览」—— 仅最近 14 天发布的视频，超期自动清理。"""
    conn = sqlite3.connect(DB_PATH)
    latest_ts = conn.execute("SELECT MAX(timestamp) FROM video_stats").fetchone()[0]
    if not latest_ts:
        conn.close()
        print("  📊 数据总览: 本地无快照，跳过 Notion 清理和同步")
        return

    rows = conn.execute("""
        SELECT title, publish_date, plays, likes, comments, shares, favorites,
               ctr, finish_rate, avg_duration_sec, profile_visits, follower_gain
        FROM video_stats WHERE timestamp = ?
        AND (status IS NULL OR status NOT IN ('私密', '自见', '未通过', '审核中', '已删除'))
        AND publish_date >= date('now', 'localtime', '-14 days')
    """, (latest_ts,)).fetchall()
    if not rows:
        conn.close()
        print("  📊 数据总览: 当前 14 天窗口无可同步视频，跳过 Notion 清理")
        return

    tracking_speeds = {}
    try:
        for row in conn.execute("""
            SELECT video_title, plays_per_hour
            FROM video_tracking
            WHERE (video_title, checkpoint_time) IN (
                SELECT video_title, MAX(checkpoint_time)
                FROM video_tracking GROUP BY video_title
            )
            AND checkpoint_time >= datetime('now', 'localtime', '-2 hours')
        """):
            tracking_speeds[row[0][:80]] = row[1]
    except sqlite3.Error:
        pass

    delta_speeds = {}
    try:
        prev_ts = conn.execute(
            "SELECT DISTINCT timestamp FROM video_stats ORDER BY timestamp DESC LIMIT 2"
        ).fetchall()
        if len(prev_ts) == 2:
            t2, t1 = prev_ts[0][0], prev_ts[1][0]
            hours_gap = (datetime.strptime(t2, "%Y-%m-%d %H:%M:%S") -
                         datetime.strptime(t1, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
            if hours_gap > 0.01:
                for row in conn.execute("""
                    SELECT a.title, (a.plays - b.plays) / ?
                    FROM video_stats a JOIN video_stats b
                    ON a.title = b.title
                    WHERE a.timestamp = ? AND b.timestamp = ?
                    AND (a.status IS NULL OR a.status NOT IN ('私密','自见','未通过','审核中','已删除'))
                """, (hours_gap, t2, t1)):
                    if row[1] > 0:
                        delta_speeds[row[0][:80]] = round(row[1], 1)
    except Exception:
        pass
    conn.close()

    existing = fetch_key_map(SOURCE_OVERVIEW, KEY_OVERVIEW)

    cleaned_empty = 0
    for page in query_pages(SOURCE_OVERVIEW):
        props = page.get("properties", {})
        title = _plain_text(props.get(KEY_OVERVIEW)).strip()
        plays = _plain_text(props.get("播放量")).strip()
        try:
            plays_num = float(plays) if plays else 0
        except ValueError:
            plays_num = 0
        if not title or plays_num == 0:
            if trash_page(page["id"]):
                cleaned_empty += 1
                existing.pop(title, None)
            time.sleep(0.06)

    active_titles = {r[0][:80] for r in rows}
    cleaned = 0
    for title, page_id in list(existing.items()):
        if title not in active_titles:
            if trash_page(page_id):
                del existing[title]
                cleaned += 1
            time.sleep(0.06)

    created, updated, failed = 0, 0, 0
    for r in rows:
        title = r[0][:80]
        plays = r[2] or 0
        eng = round(((r[3] or 0) + (r[4] or 0) + (r[5] or 0)) / max(plays, 1) * 100, 1)

        plays_per_hour = 0
        if title in tracking_speeds and tracking_speeds[title] > 0:
            plays_per_hour = round(tracking_speeds[title], 1)
        elif title in delta_speeds and delta_speeds[title] > 0:
            plays_per_hour = delta_speeds[title]
        elif r[1]:
            try:
                pub_dt = datetime.strptime(r[1], "%Y-%m-%d %H:%M:%S")
                hours = (datetime.now() - pub_dt).total_seconds() / 3600
                if hours > 0.1:
                    plays_per_hour = round(plays / hours, 1)
            except ValueError:
                pass

        fields = {
            KEY_OVERVIEW: title, "发布时间": r[1] or "", "播放量": plays,
            "点赞": r[3] or 0, "评论": r[4] or 0, "分享": r[5] or 0,
            "收藏": r[6] or 0, "5s完播率(%)": r[7] or 0,
            "完播率(%)": r[8] or 0, "均时长(s)": r[9] or 0,
            "互动率(%)": eng, "主页访问量": r[10] or 0,
            "粉丝增量": r[11] or 0, "更新时间": latest_ts,
            "均速/小时": plays_per_hour,
        }
        ok, action = upsert_page(SOURCE_OVERVIEW, KEY_OVERVIEW, title, fields, existing)
        if ok:
            created += 1 if action == "create" else 0
            updated += 1 if action == "update" else 0
        else:
            failed += 1
        time.sleep(0.06)

    print(f"  📊 数据总览: 更新 {updated} | 新增 {created} | 失败 {failed} | 清理旧数据 {cleaned} | 清空行 {cleaned_empty}")


def sync_tracking():
    """同步「最新作品追踪」—— 只保留最新一条，固定 key「最新追踪」。"""
    conn = sqlite3.connect(DB_PATH)
    meta = conn.execute("""
        SELECT video_title FROM video_tracking_meta
        WHERE tracking_active = 1 ORDER BY tracking_started DESC LIMIT 1
    """).fetchone()

    if not meta:
        conn.close()
        print("  📡 无活跃追踪视频")
        return

    active_title = meta[0]
    print(f"  🎬 追踪视频: {active_title[:30]}...")
    r = conn.execute("""
        SELECT video_title, checkpoint_time, hours_since_publish,
               plays, likes, comments, ctr5s, engagement_rate,
               avg_duration_sec, plays_per_hour, cumulative_growth,
               predicted_final_plays, predicted_tier, confidence
        FROM video_tracking
        WHERE video_title = ?
        ORDER BY checkpoint_time DESC LIMIT 1
    """, (active_title,)).fetchone()
    conn.close()

    if not r:
        print("  📡 无追踪记录")
        return

    velocity = r[9] or 0
    if velocity > 10000:
        trend = "爆发"
    elif velocity > 1000:
        trend = "增长"
    elif velocity > 100:
        trend = "平稳"
    else:
        trend = "低迷"

    fixed_key = "最新追踪"
    fields = {
        KEY_TRACKING: fixed_key,
        "检查时间": r[1],
        "已发布小时": round(r[2] or 0, 1),
        "播放量": r[3] or 0,
        "增量": round(velocity, 0),
        "点赞": r[4] or 0,
        "评论": r[5] or 0,
        "5s完播率(%)": r[6] or 0,
        "互动率(%)": r[7] or 0,
        "均时长(s)": r[8] or 0,
        "预测播放": r[11] or 0,
        "预测等级": r[12] or "",
        "置信度(%)": round((r[13] or 0) * 100, 0),
        "趋势": trend,
    }

    existing = fetch_key_map(SOURCE_TRACKING, KEY_TRACKING)
    for key, page_id in list(existing.items()):
        if key != fixed_key:
            if trash_page(page_id):
                del existing[key]
            time.sleep(0.06)

    ok, action = upsert_page(SOURCE_TRACKING, KEY_TRACKING, fixed_key, fields, existing)
    print(f"  📡 作品追踪: {action} ✅" if ok else "  📡 作品追踪: 失败 ❌")


def sync_account():
    """同步「账号总览」—— 清空全表后插入最新一条。"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT timestamp, total_fans, today_new_fans
        FROM account_stats
        WHERE total_fans > 0
        ORDER BY timestamp DESC LIMIT 1
    """).fetchall()
    conn.close()

    if not rows:
        print("  📊 账号总览: 无数据")
        return

    cleaned = 0
    for page in query_pages(SOURCE_ACCOUNT):
        if trash_page(page["id"]):
            cleaned += 1
        time.sleep(0.06)

    existing = fetch_key_map(SOURCE_ACCOUNT, KEY_ACCOUNT)
    created, updated, failed = 0, 0, 0

    for r in rows:
        ts = r[0]
        fields = {
            KEY_ACCOUNT: ts,
            "总粉丝数": r[1] or 0,
            "今日新增": r[2] or 0,
        }
        ok, action = upsert_page(SOURCE_ACCOUNT, KEY_ACCOUNT, ts, fields, existing)
        if ok:
            created += 1 if action == "create" else 0
            updated += 1 if action == "update" else 0
        else:
            failed += 1
        time.sleep(0.06)

    print(f"  📊 账号总览: 更新 {updated} | 新增 {created} | 失败 {failed} | 清理旧数据 {cleaned}")


def main():
    required = {
        "NOTION_TOKEN": NOTION_TOKEN,
        "NOTION_SOURCE_OVERVIEW": SOURCE_OVERVIEW,
        "NOTION_SOURCE_TRACKING": SOURCE_TRACKING,
        "NOTION_SOURCE_ACCOUNT": SOURCE_ACCOUNT,
    }
    missing = [
        key for key, value in required.items()
        if not value or value.startswith("YOUR_NOTION_")
    ]
    if missing:
        raise SystemExit(
            "缺少 Notion 配置，请在 ~/.codex/douyin-tool/.env 中配置: "
            + ", ".join(missing)
        )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 同步抖音 -> Notion")
    sync_overview()
    sync_tracking()
    sync_account()
    print(f"[{now_str}] 完成")


if __name__ == "__main__":
    main()
