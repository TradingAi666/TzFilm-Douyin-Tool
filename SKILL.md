---
name: douyin-creator-scraping
description: |
  Scrape video stats from the Douyin creator center (creator.douyin.com) using AppleScript
  to automate the user's logged-in Chrome. Covers the data-center/content page.
  Use when setting up periodic data collection, building a Douyin stats tracker,
  or automating export of video performance data.
tags: [douyin, chrome, applescript, scraping, macos]
---

# Douyin Creator Center Scraping

## The #1 Pitfall: macOS TCC Blocks launchd from Reading ~/Downloads

**This is the root cause of 99% of "download timeout" failures.** launchd-spawned
Python processes cannot read `~/Downloads/`, `~/Documents/`, or `~/Desktop/` due to
macOS TCC (Transparency, Consent, and Control). `glob.glob()`, `os.listdir()`,
even `osascript -e 'do shell script "ls/cp/cat ..."'` — ALL silently return empty
or zero from launchd context.

**The fix: redirect Chrome's download directory to `/tmp/`.** `/tmp` has zero TCC
restrictions. Chrome writes there freely, Python reads there freely.

```bash
# One-time setup — persists across Chrome restarts:
defaults write com.google.Chrome DownloadDirectory -string "/tmp/douyin_dl"
```

After this, download detection is trivially simple:

```python
def fetch_newest_xlsx():
    import glob
    pattern = os.path.join('/tmp/douyin_dl', '作品列表*.xlsx')
    files = glob.glob(pattern)
    if not files:
        return None, None
    newest = max(files, key=os.path.getmtime)
    return newest, os.path.basename(newest)
```

Detection time: ~2 seconds. Works identically from terminal and launchd.

### What does NOT work (field-tested 2026-05-10)

| Approach | Why |
|---|---|
| `glob.glob('~/Downloads/...')` from launchd | TCC blocks — returns `[]` |
| `osascript -e 'do shell script "ls/cp/cat ..."'` | Returns empty/0 from launchd |
| `osascript -e 'tell app "Finder" to duplicate...'` | Finder times out (>30s) even for single files |
| mtime comparison | Can't detect if glob can't list files |
| File snapshot on `~/Downloads/` | Snapshot always `set()` because glob returns empty |
| Adding python3 to Full Disk Access | macOS ignores FDA for system binaries |

## Pitfall #2: Login Session Expiry — Two Failure Modes

**Second most common failure after TCC.** Douyin creator center sessions expire
after a few days. There are TWO distinct failure modes depending on how expired the session is:

### Mode A: Recent Expiry → JSON Error File Downloaded

When the session expired recently (hours to ~1 day), clicking "导出数据" still
triggers a download — but the server returns a JSON error instead of an xlsx file:

```json
{"BaseResp":{"StatusCode":8,"StatusMessage":"用户未登录"}}
```

`openpyxl` then fails with `File is not a zip file`. **The PK header check catches this.**

### Mode B: Deep Expiry → No File Downloaded At All (2026-05-23)

When the session has been expired for longer (~1+ days), the server returns an
error response that Chrome does NOT interpret as a file download. **No file ever
appears in the download directory.** The `wait_for_download` PK check never fires
because `glob` returns `[]` every iteration. The scraper logs only:

```
点击导出: clicked:3
  等待下载... 10s
  等待下载... 20s
  ...
❌ 等待下载超时
```

**Crucially, there are NO `⚠️ 非xlsx文件` or `❌ 登录态过期` log lines** — the
file was never downloaded, so the PK validation code path is never reached.

### Degradation: Garfish App Stops Initializing

After too many reload attempts with an expired session, the page degrades further.
The Garfish micro-frontend stops initializing entirely:
- `document.body.innerText` shows only sidebar navigation (~66 chars): "高清发布 首页 活动管理..."
- Garfish container has `__garfishmockhtml__` attribute — the placeholder HTML, not the live app
- "加载中，请稍候..." text may appear
- `document.querySelectorAll('button')` returns only 1 button ("高清发布")
- "投稿列表" tab and "导出数据" button are never found

**This is the same root cause as Mode B** (expired session) but the symptoms are
different — it looks like the page failed to load, not that the download failed.

### Detection: Is It Session Expiry or Something Else?

| Symptom | Root cause |
|---|---|
| Export clicked, download timeout, NO `非xlsx/登录态过期` log | **Mode B**: session deeply expired, no file downloaded |
| Export clicked, download timeout, `⚠️ 非xlsx文件: {"BaseResp"...` | **Mode A**: session recently expired, JSON error downloaded |
| `File is not a zip file` in parse_xlsx | JSON error body passed PK check (unlikely but possible) |
| `作品列表.xlsx` is only ~130 bytes | JSON response, not real Excel (which is 6KB+) |
| `not found, total buttons:1` + bodyLen ~66 | **Degradation**: session expired, Garfish app won't init |
| Error happens suddenly after days of success | Session cookie TTL expired → needs re-login |
| Health check returns `js_ok` but scrape still fails | **Health check does NOT validate login session** — see note below |

### ⚠️ Health Check Limitation

The pre-flight health check (`health_check()`) validates **AppleScript JS access**
and **tab existence** — it does NOT validate the Douyin login session. A health
check returning `js_ok` means the AppleScript pipeline works, but the user may
still be logged out.

**Recovery:** User must re-login to `creator.douyin.com` in Chrome. No script changes needed.
After re-login, verify with a manual scrape run: `python3 ~/.codex/douyin-tool/scripts/douyin_hourly.py`

## Pitfall #3: Page Stuck on Home → Tabs/Buttons Not Found

After login, Chrome may land on the creator **home page** (`/creator-micro/home`).
The Garfish data-center app never loads there.

**Fix: force-navigate URL in `reload_page()`** — never just `reload t`:

```applescript
set URL of t to "https://creator.douyin.com/creator-micro/data-center/content"
delay 3
reload t
```

| Symptom | Root cause |
|---|---|
| `tab not found` / `refresh btn not found` | On /home not /data-center/content |
## Pre-flight Health Check (MUST run before scrape)

Before attempting any scrape, validate the AppleScript pipeline with a lightweight JS execution test. This catches 3 common silent failures **before** wasting time on a full page reload + Garfish wait cycle:

```python
def health_check():
    """Returns (ok, reason). Runs in ~2 seconds if healthy."""
    test_js = "/tmp/dy_health_check.js"
    with open(test_js, "w") as f:
        f.write("(function(){ return 'js_ok'; })()")
    
    script = f'''
    tell application "Google Chrome"
        repeat with w in windows
            repeat with t in tabs of w
                if (URL of t) contains "creator.douyin.com" then
                    set js to read POSIX file "{test_js}"
                    return execute t javascript js
                end if
            end repeat
        end repeat
        return "no_tab"
    end tell
    '''
    try:
        result = subprocess.check_output(['osascript', '-e', script], timeout=15).decode()
    except subprocess.TimeoutExpired:
        return False, "AppleScript 执行超时"
    except Exception as e:
        if "Allow JavaScript" in str(e) or "JavaScript 的功能已关闭" in str(e):
            return False, "🔧 AppleScript JS 已关闭 → Chrome 菜单勾上"
        return False, f"异常: {str(e)[:100]}"
    
    if result == "no_tab":
        return False, "📄 未找到 creator.douyin.com 标签页"
    if "js_ok" not in result:
        return False, f"⚠️ JS 返回异常: {result[:100]}"
    
    return True, "ok"
```

Call this at the top of `main()`. If it fails, send the `reason` string directly to Telegram — the user gets an actionable error message instead of a generic timeout.

**Why this matters:** AppleScript JS silently resets (Chrome updates, re-logins, desktop switches). Without this check, the scrape silently fails after 30s of polling for buttons that never appear. With it, failure is detected in ~2s with a clear fix instruction.

## Troubleshooting: "no_tab" — Diagnostic to List All Douyin Tabs

When the health check returns "no_tab" but the user says Chrome is open, run this
diagnostic to find ALL douyin-related tabs across every window. Use `execute_code`
(not `terminal`) because the AppleScript `&` string concatenation operator is
misinterpreted as shell backgrounding by the terminal tool:

```python
import subprocess

script = '''
tell application "Google Chrome"
    set output to ""
    repeat with w in windows
        repeat with t in tabs of w
            if URL of t contains "douyin" then
                set output to output & (URL of t) & " | win_id:" & (id of w) & linefeed
            end if
        end repeat
    end repeat
    if output is "" then
        return "NO_DOUYIN_TABS"
    end if
    return output
end tell
'''

result = subprocess.check_output(['osascript', '-e', script], timeout=10).decode().strip()
print(result)
```

This reveals:
- Whether ANY douyin tab exists at all
- Which sub-page each tab is on (`/content/manage`, `/data-center/content`, `/home`, etc.)
- Which Chrome window each tab lives in (useful for Spaces/Desktop debugging)

Common findings:
| Diagnostic result | Action |
|---|---|
| `NO_DOUYIN_TABS` | User needs to open `creator.douyin.com` in Chrome |
| Tab at `/creator-micro/content/manage` | Health check passes (URL contains `creator.douyin.com`) — reload_page() will navigate to `/data-center/content` |
| Tab at `/creator-micro/home` | Health check passes — reload_page() will navigate to correct page |
| Tab at `www.douyin.com/user/self` | Does NOT match — health check looks for `creator.douyin.com` specifically |
| Tab exists but on wrong Space | AppleScript may silently fail — move Chrome to current desktop |

## Prerequisites

- macOS with Google Chrome open and logged into `creator.douyin.com`
- **AppleScript JS execution enabled in Chrome:**
  View → Developer → Allow JavaScript from Apple Events
  ⚠️ This setting resets frequently (Chrome updates, re-logins, desktop switches).
- **Chrome download directory:** `defaults write com.google.Chrome DownloadDirectory -string "/tmp/douyin_dl"`
- ⚠️ **macOS desktop aware:** Chrome must be on the current active desktop/Space.
  AppleScript silently fails when Chrome is on a different Space.
- ⚠️ **"Allow JavaScript from Apple Events" can silently reset.** Symptom: `execution error: JavaScript execution via AppleScript is turned off`.
- **Chrome download directory set to `/tmp/douyin_dl/`:**
  `defaults write com.google.Chrome DownloadDirectory -string "/tmp/douyin_dl"`
- **Chrome must be on the active macOS desktop** — switching spaces can break AppleScript tab operations
- ⚠️ **macOS desktop / Space aware:** Chrome must be on the **current active macOS desktop (Space)**.
  AppleScript interactions silently fail or produce stale results when Chrome lives on a
  different Space. If the scraper suddenly stops finding tabs/buttons after working
  fine before, check whether Chrome got moved to another desktop.
- ⚠️ **"Allow JavaScript from Apple Events" can silently reset.** Chrome updates and
  re-logins can uncheck this setting. Symptom: `execution error: "Google Chrome" has
  encountered an error: JavaScript execution via AppleScript is turned off`. Fix:
  re-check the menu item.

## Architecture

The Douyin creator center uses **Garfish** (ByteDance micro-frontend framework):
- Content lives inside `#micro > #garfish_app_for_*` (ID suffix changes on reload)
- Use `document.querySelectorAll` on `document` (not garfish container)
- `document.body.innerText` works after full page load + tab switch

## Step-by-Step Flow

1. **Force-navigate to data center + reload** — `reload_page()` MUST set the URL
   to `https://creator.douyin.com/creator-micro/data-center/content` before reloading,
   NOT just call `reload t`. Douyin's login redirect often lands on the home page
   (`/creator-micro/home`), and reloading there keeps you on home — the Garfish
   data-center app never loads. The AppleScript MUST:
   ```applescript
   set URL of t to "https://creator.douyin.com/creator-micro/data-center/content"
   delay 3
   reload t
   ```
   Also: `activate` + `set active tab index` + `set index of w to 1` are required.
2. **Wait 15s** for Garfish app to initialize
3. **Switch to 投稿列表 tab** — charCode match: 25237,31295,21015,34920
4. **Click 刷新数据** — charCode: 21047,26032,25968,25454. Wait 4s.
5. **Poll for 导出数据 button** — up to 15 attempts, 2s each
6. **Click first 导出数据 button** — charCode: 23548,20986,25968,25454
7. **Wait for `作品列表*.xlsx`** in `/tmp/douyin_dl/` — poll with `glob` every 1s, validate PK header, timeout 40s
8. **Parse with openpyxl**, delete file after

## Code vs SKILL.md Consistency (2026-05-22 Fix)

The script `douyin_hourly.py` was out of sync with this SKILL.md on two critical points:
1. `reload_page()` did `reload t` without setting URL → now sets URL to `/data-center/content`
2. `wait_for_download()` called `move_latest_xlsx_via_applescript()` looking in `~/Downloads` → now uses direct `glob` on `/tmp/douyin_dl`

Both fixes are in the open-source repo. The SKILL.md was always correct — the code just wasn't following it.

## Critical: Tab MUST Be Foreground

Chrome blocks downloads triggered by JS `click()` on background tabs. Before any
interaction, the AppleScript MUST:
```
tell application "Google Chrome"
    activate
    set active tab index of w to tabIndex
    set index of w to 1
end tell
```

All three (`activate` + `active tab index` + `index of w to 1`) are required.

## AppleScript: No Unicode Escapes

Never use `\uXXXX` in JS via AppleScript — they fail silently. Use `charCodeAt()`:

```javascript
// 导出数据: 23548,20986,25968,25454
// 投稿列表: 25237,31295,21015,34920
// 刷新数据: 21047,26032,25968,25454
if(t.charCodeAt(0)===23548 && t.charCodeAt(1)===20986 ...) { ... }
```

Always write JS to `/tmp/*.js` file, then `read POSIX file` in AppleScript.

## Excel Schema

```
作品名称 | 发布时间 | 体裁 | 审核状态 | 播放量 | 完播率 | 5s完播率 |
封面点击率 | 2s跳出率 | 平均播放时长 | 点赞量 | 分享量 | 评论量 | 收藏量 |
主页访问量 | 粉丝增量
```

~99 rows per export. Percentages as decimals — multiply by 100.

## Database

```sql
CREATE TABLE video_stats (
    timestamp DATETIME, title TEXT, publish_date TEXT,
    plays INTEGER, avg_duration_sec INTEGER, ctr REAL,
    finish_rate REAL,
    likes INTEGER, comments INTEGER, shares INTEGER,
    favorites INTEGER, danmaku INTEGER, status TEXT
)
```

- `ctr` = 5s完播率 (%), stored from xlsx column 「5s完播率」
- `finish_rate` = 完播率/播放占比 (%), stored from xlsx column 「完播率」 (2026-05-23 新增)
- `avg_duration_sec` = average watch duration in seconds
- `status` = 审核状态 («公开», «自见», «私密», «未通过», etc.) — added 2026-05-23. **Must use ALTER TABLE ADD COLUMN** + explicit column names in INSERT to avoid column ordering bugs (see Pitfall #7). **Filter `status NOT IN ('自见', '私密', '未通过', '审核中', '已删除')` when syncing to Notion** — `notion_sync.py` applies this filter in the SELECT query.

**publish_date has 3 formats mixed** (short ISO / full ISO / Chinese). Always match on `title` only, never `publish_date`.

## Pitfall #8: status Field Not Stored → All Videos Synced (2026-05-23)

The scraper parsed «审核状态» from xlsx but didn't store it in the DB (line 348 reads it, line 388 didn't include it in INSERT). The sync script can push ALL videos if this filter is missing. **Fix**: ① `ALTER TABLE video_stats ADD COLUMN status TEXT` ② INSERT includes `status` with explicit column names ③ `notion_sync.py` filters `WHERE status NOT IN ('自见', '私密', '未通过', '审核中', '已删除')`. **Cleanup**: after fix, move stale Notion pages that match private video titles to trash.

## launchd Deployment

```xml
<!-- ~/Library/LaunchAgents/com.codex.douyin-tracker.plist -->
<key>StartInterval</key><integer>3600</integer>
<key>EnvironmentVariables</key>
<dict>
    <key>HOME</key><string>/Users/YOUR_USER</string>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
</dict>
```

Reload: `launchctl unload/load ~/Library/LaunchAgents/...plist`
Trigger: `launchctl kickstart -k gui/$(id -u)/com.codex.douyin-tracker`

## Pitfall #4: Douyin UI Redesign → Export Button Gone (2026-05-22)

**Douyin rolled out a new creator-center UI on 2026-05-22.** The old
`/data-center/content` page (with "投稿列表" tab and "导出数据" button) was
replaced by `/content/manage` (with "全部作品/已发布/审核中/未通过" tabs and
**no export button anywhere**).

**The fix: force-navigate to the OLD URL.** The old Garfish micro-frontend at
`/data-center/content` is still served and still has the export button.
`reload_page()` MUST set the URL before reloading — never just `reload t`:

```applescript
set URL of t to "https://creator.douyin.com/creator-micro/data-center/content"
delay 3
reload t
```

| Symptom | Root cause |
|---|---|
| `tab not found` (looking for 投稿列表) | On new `/content/manage` — tabs are 全部作品/已发布/审核中/未通过 |
| `not found, total buttons:1` (export) | New UI has NO export button, only "高清发布" |
| "投稿列表" charCode match returns nothing | The text "投稿列表" no longer exists in the new UI |

## Pitfall #5: Download Detection — Don't Move from ~/Downloads

When Chrome's `DownloadDirectory` is set to `/tmp/douyin_dl` (as recommended in
Pitfall #1), the `move_latest_xlsx_via_applescript()` approach fails because
files are never in `~/Downloads`. **Poll `/tmp/douyin_dl` directly with `glob`**
instead — it's faster (~2s vs 40s+ timeout) and more reliable:

```python
def wait_for_download(before_ts, timeout=40):
    import glob
    for i in range(timeout):
        time.sleep(1)
        pattern = os.path.join(DOWNLOADS_DIR, '作品列表*.xlsx')
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        for f in files:
            if os.path.getmtime(f) > before_ts:
                with open(f, 'rb') as fh:
                    if fh.read(2) != b'PK':  # validate real xlsx
                        ...  # login expiry check
                        continue
                return f
    return None
```

| Symptom | Root cause |
|---|---|
| `等待下载超时` after export clicked | `move_latest_xlsx_via_applescript` looking in `~/Downloads` but Chrome writes to `/tmp/douyin_dl` |
| Files in `/tmp/douyin_dl` but script can't find them | Script never checks `/tmp/douyin_dl` directly |
| AppleScript move from `~/Downloads` returns empty | File was never there |

## Pitfall #6: Stale launchd Process → Stale Data (2026-05-22)

If the scraper's last data update was hours/days ago but `launchctl list` shows it
running (PID present, exit code 0), a **zombie process** may be stuck. The process
survived a Chrome/network hiccup but never exited.

**Detection:**
```bash
# Check for multiple instances:
ps aux | grep douyin_hourly
# If older PID still running alongside new ones, kill it
```

**Fix:** Kill the stale PID, then manually trigger a run or wait for next schedule:
```bash
kill <stale_pid>
launchctl kickstart -k gui/$(id -u)/com.codex.douyin-tracker
```

**Symptoms:**
- `douyin_hourly.log` shows repeated "not found" for export button (10+ retries)
- Latest data timestamp is hours old despite job running "successfully"
- Multiple `douyin_hourly.py` processes in `ps aux`

## Pitfall #7: ALTER TABLE Column Ordering vs INSERT Without Column Names (2026-05-23)

When adding a new column (e.g. `finish_rate`) to an existing table via `ALTER TABLE`,
the column is added at the **end** of the table — regardless of where it appears in
`CREATE TABLE IF NOT EXISTS`. If the INSERT statement uses positional `VALUES`
without explicit column names, **data goes into the wrong columns**.

**Example of the bug:**
```python
# Schema after ALTER TABLE: timestamp, title, ..., danmaku, finish_rate
# But CREATE TABLE IF NOT EXISTS has finish_rate before likes
# Since table already exists, CREATE TABLE is a no-op — schema unchanged
cursor.execute(
    "INSERT INTO video_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (now, title, pub_date, plays, duration, ctr5s, finish,  # finish → position 6
     likes, comments, shares, favorites, 0)                 # but position 6 is still 'likes'
)
# Result: finish_rate column gets 0 (the last placeholder),
#         finish value goes to likes column
```

**Fix:** Always use explicit column names in INSERT after schema migration:
```python
cursor.execute(
    "INSERT INTO video_stats (timestamp, title, publish_date, plays, "
    "avg_duration_sec, ctr, finish_rate, likes, comments, shares, "
    "favorites, danmaku) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (...)
)
```

**Detection:** All rows show `finish_rate = 0` or wrong values in adjacent columns.

## Open-Source Repo (2026-05-22)

The complete tool suite is now public at [`TradingAi666/TzFilmdouyintool`](https://github.com/TradingAi666/TzFilmdouyintool)
(formerly `douyin-creator-scraping`, renamed 2026-05-22):
- `douyin_hourly.py` — hourly data scraping (AppleScript)
- `douyin_new_video_tracker.py` — video performance prediction
- `prediction_query.py` — query trending data
- `auto_reply.py` — Python wrapper for comment auto-reply
- `auto-reply/` — Node.js + Playwright browser automation for comment export/reply

## Pitfall #7: ALTER TABLE Column Ordering vs INSERT Without Column Names (2026-05-23)

When adding a new column (e.g. `finish_rate`) to an existing table via `ALTER TABLE`,
the column is added at the **end** — regardless of `CREATE TABLE IF NOT EXISTS` ordering.
If INSERT uses positional VALUES without explicit column names, **data goes into wrong columns**.

**Fix:** Always use explicit column names after schema migration:
```python
cursor.execute(
    "INSERT INTO video_stats (timestamp, title, publish_date, plays, "
    "avg_duration_sec, ctr, finish_rate, likes, comments, shares, "
    "favorites, danmaku) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (...)
)
```

**Detection:** `finish_rate = 0` for all rows despite debug showing correct values.

## Pitfall #9: Stale fcntl Lock Blocks All Scrapes (2026-05-24)

**Root cause A — `flock()` leaks through subprocess file descriptors:**
`douyin_hourly.py` uses `fcntl.flock()` to prevent concurrent Chrome access.
After acquiring the lock, it calls `subprocess.run(['python3', 'notion_sync.py'])`.
**Without `close_fds=True`**, the child process inherits ALL open file descriptors,
including `lock_fd`. `flock()` locks are attached to the **open file description**
(kernel-level), not the file descriptor. Even when the parent's `finally` block
closes `lock_fd`, the child still holds a reference → **lock never released** until
child exits (up to 300s timeout).

**Root cause B — stale lock from crash:**
If Python process crashes or is killed, 0-byte lock file persists on disk.

**Symptoms:**
- Log shows: `⚠️ 已有抓取进程在运行，跳过本次` every tick, for hours
- `ps aux | grep douyin` returns nothing — NO process running
- Lock file is 0 bytes but `flock()` returns EAGAIN

**Fix:**
```python
# In douyin_hourly.py AND douyin_new_video_tracker.py (3 locations total):
_sp.run(['python3', 'notion_sync.py'],
        timeout=300, capture_output=True, close_fds=True)  # ← close_fds=True
```

**Recovery (stale lock):**
```bash
rm -f ~/.codex/douyin-tool/.douyin_scrape.lock
```

| Symptom | Root cause |
|---|---|
| `⚠️ 已有抓取进程在运行，跳过本次` × N | Lock leaked via subprocess FDs |
| Lock persists after parent process exits | Child process inherits open file description |
| `ps aux` shows zero douyin processes | Parent exited, child still running notion_sync |

## Pitfall #10: `scrape_total_followers` — Garfish Isolation + AppleScript Fix (2026-05-24)

The `scrape_total_followers()` function must navigate to `/creator-micro/home` to extract
fans because the **Garfish micro-frontend at `/data-center/content` does NOT contain the
sidebar** in `document.body.innerText`. The sidebar (with "关注 N 粉丝 N.N万") lives in
the parent frame outside Garfish.

**Fix**: AppleScript navigates to home page with proper `exit repeat`, then runs JS:
```applescript
tell application "Google Chrome"
    activate
    repeat with w in windows
        if found then exit repeat
        repeat with t in tabs of w
            if (URL of t) contains "creator.douyin.com" then
                set URL of t to "https://creator.douyin.com/creator-micro/home"
                delay 6
                set js to read POSIX file "/tmp/dy_follower_js_v2.js"
                return execute t javascript js
            end if
        end repeat
    end repeat
end tell
```

**JS pattern**: `body.match(/\u5173\u6ce8\s*[\d,]+\s*\u7c89\u4e1d\s*([\d.]+)\s*\u4e07/)` — matches "关注 N 粉丝 N.N万".

**Timing**: Fan extraction runs at step 10 (after data-center export + Telegram report),
when navigation to home won't break anything. `close_fds=True` on subprocess prevents lock leak.

## Pitfall #11: `today_new_fans` Always 0 — Page Has No Daily New Fans Field (2026-05-24)

The JS scraping searched `document.body.innerText` for "新增" (charCode 26032, 22686)
to find today's new follower count. **The Douyin creator center home page does NOT**
contain a single-day "今日新增粉丝" metric. The page only shows:
- Total fans (e.g., 4.89万) — in sidebar
- 7-day net new ("净增粉丝 1.18万") — in data center section
- Per-followed-account "昨日 +N" — in followed accounts section

**Symptom:** `account_stats.today_new_fans` is always 0, even though `total_fans`
is increasing (48100→48900).

**Fix: Compute today_new from DB deltas in `save_account_stats()`:**

```python
def save_account_stats(data):
    current_fans = data['fans']
    today_start = datetime.now().strftime("%Y-%m-%d") + " 00:00:00"
    
    # Baseline: first entry of today's total fans
    first_today = cur.execute(
        "SELECT total_fans FROM account_stats WHERE timestamp >= ? AND total_fans > 0 ORDER BY timestamp ASC LIMIT 1",
        (today_start,)
    ).fetchone()
    
    if first_today and first_today[0] > 0:
        today_new = current_fans - first_today[0]
    else:
        # Fallback: difference from yesterday's last entry
        yesterday_end = datetime.now().strftime("%Y-%m-%d") + " 00:00:00"
        last_yesterday = cur.execute(
            "SELECT total_fans FROM account_stats WHERE timestamp < ? AND total_fans > 0 ORDER BY timestamp DESC LIMIT 1",
            (yesterday_end,)
        ).fetchone()
        today_new = current_fans - (last_yesterday[0] if last_yesterday else current_fans)
    
    cur.execute("INSERT INTO account_stats VALUES (?, ?, ?)",
                (now, current_fans, today_new))
```

**Effect:** At 12:07, `log("💾 粉丝数据已入库: 48900 (+400)")` — today_new correctly shows
400 new followers (48900 - 48500 = 400, where 48500 was the 06:06 first-of-day entry).

| Symptom | Root cause |
|---|---|
| `today_new_fans` = 0 for all rows | Page has no "新增" text; JS search fails |
| `total_fans` increases but `today_new` stays 0 | Two separate data sources: total from page works, daily doesn't exist |
| Notion account chart shows flat "今日新增" line | All DB entries have today_new=0 |

The `scrape_total_followers()` function originally used AppleScript to navigate to
`creator-micro/home`, looping through ALL Chrome windows/tabs without `exit repeat`.
With many open tabs, the `delay 5` per matching tab caused the 20s timeout.

**Fix: Remove AppleScript navigation entirely.** The creator center sidebar shows
"粉丝 N.N万" on **every page** — no need to navigate to home. Extract directly
from the current page's `document.body.innerText`.

Before (48 lines):
```python
def scrape_total_followers():
    # AppleScript: navigate to home (20s timeout!)
    script = '''tell application "Google Chrome"
        activate
        repeat with w in windows       # ← no exit repeat!
            repeat with t in tabs of w  # ← loops ALL tabs
                if URL contains "creator.douyin.com" then
                    set URL to "creator-micro/home"
                    delay 5              # ← 5s delay × N tabs → timeout
                end if
            end repeat
        end repeat
    end tell'''
    subprocess.check_output(['osascript', '-e', script], timeout=20)
    time.sleep(3)
    # ... then JS extraction
```

After (~30 lines):
```python
def scrape_total_followers():
    """从当前页面侧边栏提取粉丝总量（无需导航）"""
    js = r"""(function(){
        var result = {fans: 0};
        var body = document.body.innerText.replace(/\n/g, ' ');
        // Find 粉丝 by charCode (see Pitfall #7)
        ...
        return JSON.stringify(result);
    })()"""
    # Write JS to file → AppleScript execute → no navigation needed
    raw = run_js_file('/tmp/dy_follower_js.js', timeout=15)
```

| Symptom | Root cause |
|---|---|
| `导航首页失败: timed out after 20 seconds` | Nested loop × delay per tab × no exit |
| Fans data missing from account_stats | Function returns None on timeout |
| `粉丝: 48500 (今日+0)` but total is 48900 | Stale data from old successful run |

The scraper chains a Notion sync after Telegram report — see `skills/notion-douyin-sync.md`.

## Existing Infrastructure

- Script: `~/.codex/douyin-tool/scripts/douyin_hourly.py`
- Log: `~/.codex/douyin-tool/logs/douyin_hourly.log`
- DB: `~/.codex/douyin-tool/douyin_stats.db`
- Download dir: `/tmp/douyin_dl/`
