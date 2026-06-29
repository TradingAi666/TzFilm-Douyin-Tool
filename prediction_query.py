#!/usr/bin/env python3
"""
Douyin Video Prediction — 查询与对比追踪数据
Usage:
  python3 prediction_query.py latest      — 所有追踪视频的最新快照
  python3 prediction_query.py growth      — 所有视频的增长曲线
  python3 prediction_query.py ratios      — 24h→最终倍率（模型校准用）
  python3 prediction_query.py track <title>  — 指定视频的完整追踪记录
"""

import sqlite3
import sys
import os
from datetime import datetime
from collections import defaultdict

DB_PATH = os.environ.get("DOUYIN_DB_PATH", os.path.expanduser("~/.codex/douyin-tool/douyin_stats.db"))


def format_num(n):
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return f"{n:,}"


def latest():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT video_title, MAX(hours_since_publish), plays, ctr5s, engagement_rate,
               predicted_final_plays, predicted_tier, confidence, checkpoint_time
        FROM video_tracking
        GROUP BY video_title
        ORDER BY MAX(checkpoint_time) DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("暂无追踪数据。先用 python3 douyin_new_video_tracker.py --init --title \"...\" 初始化。")
        return

    print(f"{'视频':<30s} | {'时':>5s} | {'播放':>8s} | {'CTR':>5s} | {'互动':>5s} | {'预测':>8s} | {'层级':>10s} | {'置信度':>5s}")
    print("-" * 110)
    for r in rows:
        title = r[0][:28]
        print(f"{title:<30s} | {r[1]:>4.1f}h | {r[2]:>8,} | {r[3]:>4.1f}% | {r[4]:>4.1f}% | {r[5]:>8,} | {r[6]:>10s} | {r[7]:>4.0%}")


def growth():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT video_title, hours_since_publish, plays, ctr5s, engagement_rate,
               plays_per_hour, predicted_final_plays, predicted_tier
        FROM video_tracking
        ORDER BY video_title, hours_since_publish
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("暂无追踪数据。")
        return

    videos = defaultdict(list)
    for r in rows:
        videos[r[0]].append(r)

    for title, data in videos.items():
        print(f"\n🎬 {title[:50]}")
        print(f"   {'h':>5s} {'播放':>8s} {'CTR':>5s} {'互动':>5s} {'时速':>7s} {'预测':>8s} 层级")
        for r in data:
            print(f"   {r[1]:>4.1f}h {r[2]:>8,} {r[3]:>4.1f}% {r[4]:>4.1f}% {r[5]:>6,.0f}/h {r[6]:>8,} {r[7]}")


def ratios():
    """24h→最终倍率，用于模型校准"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT vt.video_title,
               MAX(CASE WHEN vt.hours_since_publish BETWEEN 22 AND 26 THEN vt.plays END) as p24,
               (SELECT v2.plays FROM video_tracking v2 WHERE v2.video_title = vt.video_title ORDER BY v2.hours_since_publish DESC LIMIT 1) as pfinal
        FROM video_tracking vt
        GROUP BY vt.video_title
        HAVING p24 > 0 AND pfinal > 0
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("No videos with 24h data yet.")
        return

    ratios_list = []
    print(f"{'视频':<30s} | {'24h播放':>8s} | {'最终播放':>8s} | {'倍率':>5s}")
    print("-" * 70)
    for r in rows:
        if r[1] and r[2] and r[1] > 0:
            ratio = r[2] / r[1]
            ratios_list.append(ratio)
            print(f"{r[0][:28]:<30s} | {r[1]:>8,} | {r[2]:>8,} | {ratio:>4.1f}x")

    if ratios_list:
        avg = sum(ratios_list) / len(ratios_list)
        print(f"\n平均倍率: {avg:.1f}x (n={len(ratios_list)}, 范围 {min(ratios_list):.1f}-{max(ratios_list):.1f}x)")


def track_one(title):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT video_title, hours_since_publish, plays, likes, comments, ctr5s,
               engagement_rate, plays_per_hour, predicted_final_plays, predicted_tier, confidence
        FROM video_tracking
        WHERE video_title LIKE ?
        ORDER BY hours_since_publish
    """, (f"%{title[:12]}%",))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print(f"未找到视频「{title}」的追踪记录")
        return

    print(f"🎬 {rows[0][0]}")
    print(f"   {'h':>5s} {'播放':>8s} {'CTR':>5s} {'互动':>5s} {'时速':>7s} {'预测':>8s} {'层级':>10s} {'置信度':>5s}")
    for r in rows:
        print(f"   {r[1]:>4.1f}h {r[2]:>8,} {r[5]:>4.1f}% {r[6]:>4.1f}% {r[7]:>6,.0f}/h {r[8]:>8,} {r[9]:>10s} {r[10]:>4.0%}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 prediction_query.py [latest|growth|ratios|track <title>]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "track" and len(sys.argv) > 2:
        track_one(sys.argv[2])
    else:
        {"latest": latest, "growth": growth, "ratios": ratios}.get(cmd, latest)()
