---
name: douyin-video-prediction
description: >
  Douyin video performance prediction — track new videos every 30min for 24h,
  predict final play tier based on cohort analysis (peer videos at same timepoint)
  with 3D quality correction (CTR + engagement + avg duration).
  V3 model uses video_stats for historical cohort data instead of fixed multipliers.
tags: [douyin, prediction, tracking, analytics, video]
---

# Douyin Video Performance Prediction

Predict final play volume tier based on first 24 hours of data. Uses CTR (5s完播率)
and engagement rate as quality signals, calibrated against historical 24h→final multipliers.

## Quick Start: Track a New Video

```
用户：我上传了新视频「xxx」
```

Then:
1. Check health: run AppleScript JS health check from `douyin-creator-scraping` first
2. Run a fresh scrape to ensure the new video is in `video_stats`
3. **⚠️ Verify the title**: Parse the downloaded xlsx directly to confirm the current title. Titles can change after publishing; trusting `video_stats` alone risks matching the old/stale title. Quick check: `python3 -c "import openpyxl,glob; wb=openpyxl.load_workbook(max(glob.glob('/tmp/douyin_dl/作品列表*.xlsx'), key=__import__('os').path.getmtime)); [print(r[0][:50],'|',r[4]) for r in wb.active.iter_rows(min_row=2, values_only=True) if 'keyword_from_user' in str(r[0])]"`
4. **Patch `TARGET_TITLE`** in the script to the verified title from step 3
5. Run `python3 ~/.codex/douyin-tool/scripts/douyin_new_video_tracker.py --init`
6. Create cron job: `every 30m`, repeat 48, deliver to origin, skills=`["douyin-creator-scraping","douyin-video-prediction"]`
7. Cron prompt: `运行 python3 ~/.codex/douyin-tool/scripts/douyin_new_video_tracker.py，把输出的追踪报告原样发送给用户。不要做任何额外处理或总结，直接输出脚本结果即可。`

**If the user uploads multiple new videos at once**, the script can only track one at a time (single `TARGET_TITLE`). Track the most recent one first, then ask whether to also track the others. To track a second video simultaneously, patch `TARGET_TITLE` to the second title and create a separate cron job — but the title will be overwritten each run, so parallel tracking requires a wrapper script or script modification.

## Database Schema

Two tables in `~/.codex/douyin-tool/douyin_stats.db`:

### `video_tracking` — per-checkpoint snapshots
```sql
CREATE TABLE video_tracking (
    video_title TEXT,          -- full title
    publish_date TEXT,         -- publish timestamp
    checkpoint_time TEXT,      -- when this snapshot was taken
    hours_since_publish REAL,  -- hours elapsed since publish
    plays INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    favorites INTEGER,
    ctr5s REAL,                -- 5-second completion rate (%)
    avg_duration_sec REAL,
    engagement_rate REAL,      -- (likes+comments+shares)/plays * 100
    plays_per_hour REAL,       -- instant velocity
    cumulative_growth INTEGER, -- plays since baseline
    predicted_final_plays INTEGER,
    predicted_tier TEXT,       -- 💎10万+ / 🥇5-10万 / 🥈2-5万 / 🥉1-2万 / 📦<1万
    confidence REAL            -- 0.0-1.0, increases with hours_since_publish
);
```

### `video_tracking_meta` — per-video baseline
```sql
CREATE TABLE video_tracking_meta (
    video_title TEXT PRIMARY KEY,
    publish_date TEXT,
    tracking_started TEXT,
    baseline_plays INTEGER,
    tracking_active INTEGER DEFAULT 1
);
```

## Prediction Formula (v3 — Cohort-Based, 3D Quality)

```
predicted_final = current_plays × cohort_ratio × ctr_mod × eng_mod × dur_mod

cohort_ratio = median(historical final_plays / plays_at_same_hour)
  - Queries video_stats (not video_tracking) for all historical videos at the SAME hours_since_publish
  - Uses median to resist outliers (one viral video doesn't skew everything)
  - At <1h, capped at 5000x to prevent absurd early predictions
  - Ratio naturally converges as hours increase: ~500-2000x at 0.5h, ~50-500x at 2h, ~5-20x at 8h, ~1.5-4x at 24h

CTR modifier (percentile-based vs peers at same timepoint):
  ≥ 80th percentile → 1.25
  ≥ 60th percentile → 1.1
  ≥ 40th percentile → 1.0
  ≥ 20th percentile → 0.85
  else             → 0.65
  (Fallback to absolute thresholds if <2 peers: ≥50→1.15, ≥40→1.0, ≥30→0.85, ≥20→0.7, else 0.5)

Engagement modifier (same percentile approach):
  ≥ 80th percentile → 1.25
  ≥ 60th percentile → 1.1
  ≥ 40th percentile → 1.0
  ≥ 20th percentile → 0.85
  else             → 0.65
  (Fallback: ≥8→1.2, ≥5→1.1, ≥3→1.0, ≥1.5→0.85, else 0.7)

Duration modifier (avg_duration_sec — same percentile approach):
  ≥ 80th percentile → 1.2
  ≥ 60th percentile → 1.1
  ≥ 40th percentile → 1.0
  ≥ 20th percentile → 0.9
  else             → 0.75
  (Fallback: 1.0 — neutral when no peer data)

Confidence:
  < 2h  → 0.25 (+ cohort_bonus 0.02 per peer, max 0.1)
  < 4h  → 0.4
  < 8h  → 0.55
  < 12h → 0.7
  < 24h → 0.8
  ≥ 24h → 0.9
  Cap: 0.92

Key improvement over v2: The ratio is time-aware. At 0.5h, the model uses 0.5h→final ratios
from history (typically 500-2000x). At 8h, it uses 8h→final ratios (typically 5-20x).
At 24h, it converges to the old 24h→final ratio (typically 1.5-4x).
Quality correction uses peer-relative percentile ranking instead of absolute thresholds.
Three quality dimensions multiply together: a video strong in all 3 gets up to 1.8x boost
(1.25×1.25×1.2), while a weak one gets 0.32x penalty (0.65×0.65×0.75).
```

## Switching Tracking Targets

When tracking a new video while a previous one is still active:

1. **Deactivate old tracking**: `UPDATE video_tracking_meta SET tracking_active=0 WHERE video_title LIKE '%old_keyword%'`
2. **Patch `TARGET_TITLE`** in `douyin_new_video_tracker.py` to the new video's full title (first 12+ chars — the script's `query_video()` matches by prefix)
3. **Run `--init`** for the new video to establish the baseline
4. **Replace the cron job** — remove the old one (`cronjob action=remove job_id=...`) and create a new one with the same schedule but updated name and prompt referencing the new title
5. Verify with `SELECT video_title, tracking_active FROM video_tracking_meta` that only one video is active

The old video's tracking data is preserved in `video_tracking` for historical comparison — only `tracking_active` is flipped to 0.

## Post-hoc Analysis: 5s完播率 × 完播率 × 平均时长 (2026-05-23)

For analyzing past videos' performance patterns, query the three quality metrics
together from `video_stats` (requires `finish_rate` column — see scraper update):

```sql
SELECT title, MAX(plays) as plays, ctr as ctr5s, finish_rate, avg_duration_sec
FROM video_stats
WHERE finish_rate IS NOT NULL AND finish_rate > 0
GROUP BY title HAVING MAX(plays) >= 100000
ORDER BY MAX(plays) DESC
```

Key patterns discovered across 16 ten-wan+ videos:

1. **完播率都很低 (1.8-5.1%)** — this is normal. 99% of viewers never finish any Douyin video.
2. **完播率高 ≠ 播放高** — the highest 完播率 (永辉 5.1%) had the lowest plays (12.7万).
   完播率 reflects content depth, not virality.
3. **5s完播率 ÷ 完播率 = 传播基因** — the sweet spot is 10-15x. Lower means narrow
   audience (hardcore content), higher means shallow but viral (clickbait).
4. **百万级爆款的共性**: 5s完播 44-55% + 分享/收藏任一项破万. 完播率 2-5% 都是正常的.

The `finish_rate` (完播率) was added to the scraper on 2026-05-23 (extracted from
xlsx column 「完播率」). Historical data before this date lacks this field.

## Historical Comparison

Compare first-N-hours performance across recent videos.

See also: `references/metrics-analysis.md` — 破十万视频的三维共性分析框架 (5s完播率 × 完播率 × 平均时长)。

```sql
-- Find 24h play count vs final play count for all tracked videos
SELECT vt.video_title,
       MAX(CASE WHEN vt.hours_since_publish BETWEEN 22 AND 26 THEN vt.plays END) as plays_24h,
       MAX(vt.plays) as plays_final,
       ROUND(MAX(vt.plays)*1.0 / MAX(CASE WHEN vt.hours_since_publish BETWEEN 22 AND 26 THEN vt.plays END), 1) as ratio
FROM video_tracking vt
GROUP BY vt.video_title
HAVING plays_24h > 0;
```

```sql
-- Growth curve comparison (multiple videos at same timepoints)
SELECT video_title, hours_since_publish, plays, ctr5s, engagement_rate
FROM video_tracking
WHERE video_title IN (SELECT DISTINCT video_title FROM video_tracking_meta WHERE tracking_active=1)
ORDER BY video_title, hours_since_publish;
```

## Script: douyin_new_video_tracker.py

Location: `~/.codex/douyin-tool/scripts/douyin_new_video_tracker.py`

Usage:
```bash
# First time tracking a video: initialize baseline
python3 ~/.codex/douyin-tool/scripts/douyin_new_video_tracker.py --init

# Subsequent runs: scrape + compare + predict + save
python3 ~/.codex/douyin-tool/scripts/douyin_new_video_tracker.py

# Skip scrape — read from existing data in DB (use when hourly scraper is already running)
python3 ~/.codex/douyin-tool/scripts/douyin_new_video_tracker.py --no-scrape

# Track a different video (update TARGET_TITLE in script first)
```

Flow (default mode):
1. Runs `douyin_hourly.py` to scrape fresh data from Douyin creator center
2. Queries `video_stats` for the target video
3. Compares against last checkpoint in `video_tracking`
4. Calculates velocity, engagement, prediction
5. Saves new checkpoint to `video_tracking`
6. Runs `notion_sync.py` to push to Notion
7. Prints Telegram-formatted HTML report

`--no-scrape` mode: Skips step 1 (the hourly scraper cron already updates `video_stats`). Runs in seconds instead of 2+ minutes. **Recommended for cron jobs** — schedule the hourly scraper separately, use `--no-scrape` for 30-minute tracking checkpoints.

## Prerequisites

- `douyin-creator-scraping` skill must be configured (Chrome with AppleScript JS enabled, download dir set to `/tmp/douyin_dl/`)
- `douyin_hourly.py` must exist at `~/.codex/douyin-tool/scripts/douyin_hourly.py`
- `douyin_stats.db` must exist with `video_stats` table populated

## Pitfalls

- **Prediction is coarse early on**: confidence < 35% before 2h, < 55% before 8h. The model becomes reliable after 8-12h when CTR/duration stabilize and cohort ratios converge.
- **CTR naturally decays**: 5s完播率 drops as the video reaches broader audiences. The v3 percentile-based model compares against peers at the same timepoint, so this decay is already priced in — a 45% CTR at 8h isn't penalized if peers are also at ~35%.
- **Cohort data source**: Uses `video_stats` (raw scraped data, 8+ videos with full lifecycle) — not `video_tracking` (which only has actively-tracked videos). This gives much richer peer data even when no other video is being actively tracked.
- **Three quality dimensions multiply**: A video crushing all 3 dimensions can get a combined boost of 1.8x (1.25×1.25×1.2). A video weak in all 3 gets penalized to 0.32x (0.65×0.65×0.75). The model is sensitive to multi-dimensional weakness — one bad dimension can be offset by others, but being bad across the board compounds.
- **Don't override TARGET_TITLE lightly**: The script matches by first 12 chars. If you change the title constant, also `DELETE FROM video_tracking_meta WHERE video_title LIKE '%old%'` before --init.
- **Re-init cleanup**: Running `--init` on a video that was already tracked creates a fresh baseline. If you accidentally --init the wrong video, clean up with `DELETE FROM video_tracking_meta WHERE video_title LIKE '%<title>%'` and `DELETE FROM video_tracking WHERE video_title LIKE '%<title>%'`.
- **Baseline overwrite bug (fixed 2026-05-11)**: `save_checkpoint()` uses `INSERT OR IGNORE` on `video_tracking_meta` — baseline is only set once at --init time. If cumulative growth numbers look wrong (tiny deltas despite large total plays), the baseline may have been overwritten by an older version. Recover with:
  ```sql
  UPDATE video_tracking_meta SET baseline_plays = (
    SELECT plays FROM video_stats WHERE title LIKE '%<title>%' ORDER BY timestamp ASC LIMIT 1
  ) WHERE video_title LIKE '%<title>%';
  ```
- **`--title` CLI flag is NOT implemented (as of 2026-05-16)**: The script's docstring says `[--title "视频标题"]` but `main()` only checks `--init`. To switch tracking targets, you **must patch** the `TARGET_TITLE` constant in the script source. If you try `--title` it will be silently ignored and the script will track whatever `TARGET_TITLE` is set to. This is the #1 cause of "why is it tracking the wrong video" bugs.
- **Single-video tracking only**: The script matches `TARGET_TITLE` by first 12 chars via `query_video()`. Only one video can be tracked per cron job. For multiple simultaneous videos, you need separate cron jobs — but since each run uses the live `TARGET_TITLE` constant, parallel tracking requires either a wrapper script that patches the constant before each call, or a script modification to support per-run title override.
- **Baseline overwrite bug in meta table (fixed 2026-05-23)**: `save_checkpoint()` stored `plays_prev` (the 3rd parameter, which is 0 during `--init`) into `video_tracking_meta.baseline_plays`, instead of the actual `baseline_plays` value. This caused `cumulative_growth = plays - 0 = plays` instead of `plays - baseline`. **Fix**: line ~349 now stores `baseline_plays` (the function parameter) instead of `plays_prev`. **Recovery for old data**: `UPDATE video_tracking_meta SET baseline_plays = 129 WHERE video_title LIKE '%<title>%'` then recalculate all cumulative_growth in video_tracking: `UPDATE video_tracking SET cumulative_growth = plays - 129 WHERE video_title LIKE '%<title>%'`.
- **plays_per_hour hardcoded /0.5 (fixed 2026-05-23)**: The old formula `(plays_diff / 0.5)` assumed a fixed 30-minute interval between checkpoints. When actual intervals varied (minutes to hours), velocity was wildly wrong. **Fix**: `save_checkpoint()` now queries the previous checkpoint time from DB and computes actual delta_hours. Velocity = plays_diff / delta_hours. This is critical when checkpoints are sparse (e.g., scraper was down for hours).
- **Notion sync timeout in tracker**: The tracker calls `notion_sync.py` with `timeout=300` in both init and checkpoint paths. If Notion is slow or credentials are wrong, DB data is still saved and the next run can sync again.
- **Stale tracking data cleanup**: After scraper recovery from login expiry or JS failure, `video_tracking` may contain frozen checkpoints (identical plays across multiple rows). **Cleanup procedure**: ① `DELETE FROM video_tracking WHERE plays = <frozen_value>` ② Move corresponding stale Notion pages to trash if needed ③ `python3 notion_sync.py` to re-sync clean data.
- **Violation/限流 awareness**: The prediction model assumes normal traffic distribution. If a video is flagged for violation and under appeal (限流/压制), actual plays will grow much slower than predicted. Use the tracking reports to detect this: if plays/hour stays < 10-20 while CTR is healthy (>35%), the video is likely being throttled. Once the appeal is resolved and suppression lifts, you should see an immediate velocity spike in the next 1-2 checkpoints.
- **Title change mid-tracking (data freeze)**: If the user edits the video title in Douyin after publishing, the creator center xlsx export will contain the NEW title, while the old title's rows in `video_stats` stop updating. The tracker will keep matching the old `TARGET_TITLE` → stale data (e.g., 39 plays frozen while actual is 1500+). **Detection**: If 3+ scrapes show identical play count for the tracked video, suspect a title change. **Fix**: ① Parse the xlsx directly to find the new title (search by partial keyword), ② Update `TARGET_TITLE` in the script, ③ `DELETE FROM video_tracking_meta WHERE video_title LIKE '%old%'`, ④ `DELETE FROM video_tracking WHERE video_title LIKE '%old%'`, ⑤ Re-run `--init`. The cron job's script will pick up the new title automatically since TARGET_TITLE is patched in source.
- **Title change → stale data trap (2026-05-17)**: Douyin allows creators to rename videos after publishing. When the title changes, `video_stats` retains the old title with **frozen play counts** — the scraper's xlsx export contains the new title with live data, but `query_video()` matches the stale old title. **Symptoms**: play count stays flat (e.g. 39 plays for hours) while user reports much higher numbers. **Detection**: after `--init`, if user says data is wrong, parse the latest xlsx directly: `python3 -c "import openpyxl; wb=openpyxl.load_workbook('/tmp/douyin_dl/作品列表*.xlsx'); [print(r[0], r[4]) for r in wb.active.iter_rows(min_row=2, values_only=True) if 'keyword' in str(r[0])]"` to find the live title. **Fix**: (1) `DELETE FROM video_tracking_meta WHERE video_title LIKE '%old_title_part%'` (2) `DELETE FROM video_tracking WHERE video_title LIKE '%old_title_part%'` (3) patch `TARGET_TITLE` to the current title from the xlsx (4) re-run `--init`. Also delete and recreate the cron job if its schedule format was wrong.
- **Scraper silent failure → frozen tracking data (2026-05-22, updated 2026-05-23)**: `douyin_hourly.py` returns exit code 0 on failure — it sends a Telegram error notification but exits cleanly. The tracker calls it with `subprocess.run()` and sees exit code 0, prints `[tracker] ✅ 抓取完成`, then proceeds to read data from `video_stats`. If the scraper failed, `video_stats` has stale data (last successful scrape may be hours old). The tracker then reports "间隔增量：0" and "趋势：⚠️ 低迷" — which looks like real low-growth data but is actually frozen. **Root causes (not just disabled JS)**: (a) AppleScript JS disabled in Chrome, (b) **Douyin login session expired** (most common after 1+ days — see douyin-creator-scraping Pitfall #2 Mode B), (c) Chrome on wrong desktop/Space, (d) Network issues. **Detection for the agent**: If 2+ consecutive checkpoints show identical plays with "间隔增量：0", suspect scraper failure. Check `douyin_hourly.log` for recent entries: if the latest entry is missing expected steps (no "页面刷新+激活", no "等待页面加载", or has "等待下载超时" with no "非xlsx文件" log), the scraper failed silently. **Do NOT trust tracker data with "间隔增量：0"** — run the AppleScript health check and verify the last `video_stats` timestamp is recent. **Recovery**: depends on root cause — re-login to creator.douyin.com, re-enable JS in Chrome, or move Chrome to active desktop. The scraper auto-sends Telegram error notifications on failure; these come from the script, not the agent.\n- **Stale tracking cleanup**: After the scraper recovers, stale checkpoints (identical plays across multiple rows) remain in DB and may remain in Notion. **Cleanup procedure**: ① Delete stale DB rows: `DELETE FROM video_tracking WHERE video_title LIKE '%<title>%' AND plays = <frozen_value>` ② Move stale Notion pages to trash if needed. ③ Re-sync: `python3 ~/.codex/douyin-tool/scripts/notion_sync.py`. **Verification**: after cleanup, run `SELECT checkpoint_time, plays FROM video_tracking WHERE video_title LIKE '%<title>%' ORDER BY checkpoint_time` — all checkpoints should show increasing plays.\n- **Tracker hang → manual checkpoint (2026-05-23)**: When `douyin_new_video_tracker.py` times out (it runs a full scrape first, and Chrome may be down), bypass the scrape entirely. Insert a checkpoint directly from `video_stats` (which already has fresh data from the most recent successful scraper run) using execute_code: SELECT latest stats, compute deltas vs previous checkpoint, INSERT INTO video_tracking, then run `notion_sync.py` to push. No need to wait for Chrome recovery — the hourly scraper cron will update video_stats independently.\n- **NULL values in video_stats → TypeError crashes (fixed 2026-05-19)**: The `video_stats` table can contain NULLs in engagement fields (likes, comments, shares, ctr5s, avg_duration_sec) — especially for recently published videos. The script has 7 defensive guards across all arithmetic and formatting sites: (a) `format_num()`/`format_delta()` — return "0"/0 for None, (b) `predict_final_plays()` — `ctr5s = ctr5s or 0` etc. at function entry, (c) `save_checkpoint()` — `likes = video['likes'] or 0` before arithmetic, (d) `main()` engagement calc — same, (e) init path — same, (f) display lines — `video['ctr5s'] or 0:.1f`. If you add new columns or display logic, apply the same guard. Symptoms: `TypeError: unsupported operand type(s) for +: 'NoneType' and 'NoneType'` or `TypeError: '>=' not supported between instances of 'NoneType' and 'int'`.
