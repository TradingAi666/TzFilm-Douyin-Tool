#!/usr/bin/env python3
"""
抖音每小时数据追踪 v3
- 切换到"投稿列表"标签，点击第一个"导出数据"按钮下载 Excel（作品列表.xlsx）
- 解析全部视频数据存入 SQLite 数据库
- Telegram 汇报最新 3 条
"""

import subprocess
import time
import sqlite3
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime
import sys

DB_PATH = os.path.expanduser("~/.codex/douyin-tool/douyin_stats.db")
LOG_PATH = os.path.expanduser("~/.codex/douyin-tool/logs/douyin_hourly.log")
ENV_PATH = os.path.expanduser("~/.codex/douyin-tool/.env")
# 用绝对路径，优先读 HOME 环境变量，兜底用 expanduser
_HOME = os.environ.get('HOME') or os.path.expanduser('~')
# Python 没有 TCC 权限读 ~/Downloads，所以让 AppleScript 把文件搬到 /tmp 来
DOWNLOADS_DIR = '/tmp/douyin_dl'
SOURCE_DIR = os.path.join(_HOME, 'Downloads')  # Chrome 真实下载位置
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


def move_latest_xlsx_via_applescript(before_ts):
    """用 do shell script（享有用户级 TCC 权限）查找并搬运 ~/Downloads 里
    before_ts 之后的最新 '作品列表*.xlsx' 到 /tmp/douyin_dl/。返回新路径或 None。
    """
    # 把 shell 命令写成独立脚本文件，避开 AppleScript 字符串转义问题
    sh_path = '/tmp/dy_move_xlsx.sh'
    sh_content = f'''#!/bin/bash
SRC={SOURCE_DIR!r}
DST={DOWNLOADS_DIR!r}
BEFORE={int(before_ts)}
newest=""
newest_mtime=0
for f in "$SRC"/作品列表*.xlsx; do
  [ -e "$f" ] || continue
  mtime=$(stat -f %m "$f" 2>/dev/null)
  [ -z "$mtime" ] && continue
  if [ "$mtime" -gt "$BEFORE" ] && [ "$mtime" -gt "$newest_mtime" ]; then
    newest="$f"
    newest_mtime=$mtime
  fi
done
if [ -n "$newest" ]; then
  base=$(basename "$newest")
  dst="$DST/$base"
  mv "$newest" "$dst" && echo "$dst"
fi
'''
    try:
        with open(sh_path, 'w') as f:
            f.write(sh_content)
        os.chmod(sh_path, 0o755)
    except Exception as e:
        log(f"⚠️ 写脚本失败: {e}")
        return None

    # AppleScript 调用 shell 脚本（do shell script 享有用户 TCC 权限）
    script = f'do shell script "{sh_path}"'
    try:
        result = subprocess.check_output(
            ['osascript', '-e', script], timeout=15, stderr=subprocess.STDOUT
        ).decode('utf-8').strip()
        if result and result.startswith('/') and os.path.exists(result):
            return result
        return None
    except subprocess.CalledProcessError as e:
        out = e.output.decode('utf-8', errors='replace').strip()
        # 找不到文件 / 输出为空都不算错
        if out:
            log(f"⚠️ 搬运异常: {out}")
        return None
    except Exception as e:
        log(f"⚠️ 搬运异常: {e}")
        return None


def _load_env_var(key):
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except:
        pass
    return os.environ.get(key, "")


TELEGRAM_BOT_TOKEN = _load_env_var("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _load_env_var("TELEGRAM_HOME_CHANNEL") or "5422098786"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                log("✅ Telegram 消息发送成功")
            else:
                log(f"⚠️  Telegram 发送失败: {result}")
    except Exception as e:
        log(f"❌ Telegram 发送异常: {e}")


def run_js_file(js_path, tab_contains="creator.douyin.com", timeout=30):
    script = f'''
    tell application "Google Chrome"
        repeat with w in windows
            repeat with t in tabs of w
                set u to URL of t
                if u contains "{tab_contains}" then
                    set js to read POSIX file "{js_path}"
                    return execute t javascript js
                end if
            end repeat
        end repeat
        return ""
    end tell
    '''
    try:
        result = subprocess.check_output(
            ['osascript', '-e', script], timeout=timeout
        ).decode('utf-8').strip()
        return result
    except Exception as e:
        log(f"❌ AppleScript 执行失败: {e}")
        return None


def reload_page():
    """激活抖音标签到前台，强制导航到旧版 data-center/content（新版 /content/manage 没有导出按钮），然后刷新"""
    script = '''
    tell application "Google Chrome"
        activate
        set found to false
        repeat with w in windows
            set tabIndex to 0
            repeat with t in tabs of w
                set tabIndex to tabIndex + 1
                if (URL of t) contains "creator.douyin.com" then
                    set active tab index of w to tabIndex
                    set index of w to 1
                    set URL of t to "https://creator.douyin.com/creator-micro/data-center/content"
                    delay 3
                    reload t
                    set found to true
                    exit repeat
                end if
            end repeat
            if found then exit repeat
        end repeat
        if found then
            return "reloaded"
        else
            return "not found"
        end if
    end tell
    '''
    try:
        res = subprocess.check_output(['osascript', '-e', script], timeout=30).decode().strip()
        log(f"页面刷新+激活: {res}")
        return "reloaded" in res
    except Exception as e:
        log(f"❌ 刷新失败: {e}")
        return False


def scrape_total_followers():
    """导航到首页提取粉丝，今日新增由 save_account_stats 从 DB 差值计算"""
    # AppleScript: 找到 creator.douyin.com 标签 → 导航到首页 → 等待加载 → 执行 JS
    script = '''
    tell application "Google Chrome"
        activate
        set found to false
        repeat with w in windows
            if found then exit repeat
            set tabIndex to 0
            repeat with t in tabs of w
                set tabIndex to tabIndex + 1
                if (URL of t) contains "creator.douyin.com" then
                    set active tab index of w to tabIndex
                    set index of w to 1
                    set URL of t to "https://creator.douyin.com/creator-micro/home"
                    delay 6
                    set js to read POSIX file "/tmp/dy_follower_js_v2.js"
                    set jsResult to execute t javascript js
                    return jsResult
                end if
            end repeat
        end repeat
        return "no_tab"
    end tell
    '''
    try:
        raw = subprocess.check_output(['osascript', '-e', script], timeout=30).decode('utf-8').strip()
        if raw and raw != 'no_tab':
            data = json.loads(raw)
            fans = int(data.get('fans', 0))
            log(f"粉丝: {fans} | head={data.get('bodyHead','')[:80]}")
            return {'fans': fans}
        elif raw == 'no_tab':
            log("粉丝抓取: 未找到 creator.douyin.com 标签")
    except Exception as e:
        log(f"粉丝抓取失败: {e}")
    return None


def save_account_stats(data):
    """存入 account_stats 表，今日新增从 DB 差值计算"""
    if not data:
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS account_stats (
            timestamp DATETIME, total_fans INTEGER, today_new_fans INTEGER
        )
    """)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_fans = data['fans']

    # 今日新增 = 当前总粉丝 - 今日首次抓取的总粉丝（或昨日最后的总粉丝）
    today_start = datetime.now().strftime("%Y-%m-%d") + " 00:00:00"
    first_today = cur.execute(
        "SELECT total_fans FROM account_stats WHERE timestamp >= ? AND total_fans > 0 ORDER BY timestamp ASC LIMIT 1",
        (today_start,)
    ).fetchone()

    if first_today and first_today[0] > 0:
        today_new = current_fans - first_today[0]
    else:
        # 今天还没抓过，用昨天最后一次作为基准
        yesterday_end = datetime.now().strftime("%Y-%m-%d") + " 00:00:00"
        last_yesterday = cur.execute(
            "SELECT total_fans FROM account_stats WHERE timestamp < ? AND total_fans > 0 ORDER BY timestamp DESC LIMIT 1",
            (yesterday_end,)
        ).fetchone()
        if last_yesterday and last_yesterday[0] > 0:
            today_new = current_fans - last_yesterday[0]
        else:
            today_new = 0

    cur.execute("INSERT INTO account_stats VALUES (?, ?, ?)",
                (now, current_fans, today_new))
    conn.commit()
    conn.close()
    log(f"💾 粉丝数据已入库: {current_fans} (+{today_new})")


def switch_to_list_tab():
    """切换到投稿列表标签"""
    js = """
(function(){
    // 找所有 cursor:pointer 的元素，匹配 charCode 为投稿列表
    var all = Array.from(document.querySelectorAll('*'));
    for(var i=0; i<all.length; i++) {
        var el = all[i];
        var t = (el.innerText || '').trim();
        // 投(25237)稿(31295)列(21015)表(34920) 且文字很短（就是标签本身）
        if(t.charCodeAt(0)===25237 && t.charCodeAt(1)===31295 && t.charCodeAt(2)===21015 && t.charCodeAt(3)===34920 && t.length === 4) {
            el.click();
            return 'switched';
        }
    }
    return 'tab not found';
})()
"""
    tmp = "/tmp/dy_switch_tab.js"
    with open(tmp, "w") as f:
        f.write(js)
    result = run_js_file(tmp)
    log(f"切换投稿列表: {result}")
    return result == "switched"


def click_export_button():
    """点击投稿列表区域的第一个导出数据按钮"""
    js = """
(function(){
    // 用 querySelectorAll('button') 直接找，避免 unicode 转义问题
    var btns = Array.from(document.querySelectorAll('button'));
    var exportBtns = btns.filter(function(b){
        var t = (b.innerText || '').trim();
        // 导(23548)出(20986)数(25968)据(25454)
        return t.charCodeAt(0)===23548 && t.charCodeAt(1)===20986 && t.charCodeAt(2)===25968 && t.charCodeAt(3)===25454;
    });
    if(exportBtns.length > 0) {
        exportBtns[0].click();
        return 'clicked:' + exportBtns.length;
    }
    return 'not found, total buttons:' + btns.length;
})()
"""
    tmp = "/tmp/dy_click_export.js"
    with open(tmp, "w") as f:
        f.write(js)
    result = run_js_file(tmp)
    log(f"点击导出: {result}")
    return bool(result and result.startswith('clicked'))


def wait_for_download(before_ts, timeout=40):
    """Chrome 直接下载到 /tmp/douyin_dl，直接轮询检测"""
    import glob as _glob
    for i in range(timeout):
        time.sleep(1)
        pattern = os.path.join(DOWNLOADS_DIR, '作品列表*.xlsx')
        files = sorted(_glob.glob(pattern), key=os.path.getmtime, reverse=True)
        for f in files:
            mtime = os.path.getmtime(f)
            if mtime > before_ts:
                # 验证是真正的 xlsx（PK header）
                with open(f, 'rb') as fh:
                    header = fh.read(2)
                if header != b'PK':
                    body = ''
                    try:
                        with open(f, 'r', errors='ignore') as fh2:
                            body = fh2.read(200)
                    except:
                        pass
                    if '未登录' in body or 'StatusCode' in body:
                        log(f"❌ 登录态过期: {body[:100]}")
                    else:
                        log(f"⚠️ 非xlsx文件({len(body)}B): {body[:80]}")
                    os.remove(f)
                    continue
                time.sleep(0.5)
                log(f"✅ 下载完成: {os.path.basename(f)}")
                return f
        if (i + 1) % 10 == 0:
            log(f"  等待下载... {i+1}s")
    log("❌ 等待下载超时")
    return None


def parse_xlsx(filepath):
    """解析 Excel，返回所有视频数据"""
    try:
        import openpyxl
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'openpyxl', '-q'])
        import openpyxl

    try:
        wb = openpyxl.load_workbook(filepath)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            log("❌ Excel 文件为空")
            return []

        headers = [str(h).strip() if h else '' for h in rows[0]]
        log(f"Excel 表头: {headers}")
        data_rows = [r for r in rows[1:] if any(r)]

        # 列索引
        def col(keywords):
            for kw in keywords:
                for i, h in enumerate(headers):
                    if kw in h:
                        return i
            return None

        title_idx   = col(['作品名称', '名称', '标题']) or 0
        date_idx    = col(['发布时间', '时间']) or 1
        play_idx    = col(['播放量', '播放']) or 4
        finish_idx  = col(['完播率']) 
        ctr5s_idx   = col(['5s完播'])
        ctrcov_idx  = col(['封面点击'])
        bounce_idx  = col(['2s跳出', '跳出'])
        dur_idx     = col(['平均播放时长', '时长'])
        status_idx  = col(['审核状态', '状态'])
        like_idx    = col(['点赞量', '点赞'])
        share_idx   = col(['分享量', '分享'])
        comment_idx = col(['评论量', '评论'])
        fav_idx     = col(['收藏量', '收藏'])
        profile_idx = col(['主页访问量', '主页访问'])
        follower_idx= col(['粉丝增量', '涨粉'])

        def safe_float(row, idx, default=0.0):
            if idx is None: return default
            try:
                v = row[idx]
                if v is None or str(v).strip() in ('-', '', 'None'): return default
                return float(str(v).replace('%', '').replace(',', '').strip())
            except:
                return default

        def safe_int(row, idx, default=0):
            return int(safe_float(row, idx, default))

        videos = []
        for row in data_rows:
            title    = str(row[title_idx]).strip() if row[title_idx] else 'Unknown'
            pub_date = str(row[date_idx]).strip() if row[date_idx] else ''
            plays    = safe_int(row, play_idx)
            ctr5s    = round(safe_float(row, ctr5s_idx) * 100, 2) if ctr5s_idx and safe_float(row, ctr5s_idx) < 1 else safe_float(row, ctr5s_idx)
            ctrcov   = round(safe_float(row, ctrcov_idx) * 100, 2) if ctrcov_idx and safe_float(row, ctrcov_idx) < 1 else safe_float(row, ctrcov_idx)
            bounce   = round(safe_float(row, bounce_idx) * 100, 2) if bounce_idx and safe_float(row, bounce_idx) < 1 else safe_float(row, bounce_idx)
            finish   = round(safe_float(row, finish_idx) * 100, 2) if finish_idx and safe_float(row, finish_idx) < 1 else safe_float(row, finish_idx)
            duration = round(safe_float(row, dur_idx), 1)
            likes    = safe_int(row, like_idx)
            shares   = safe_int(row, share_idx)
            comments = safe_int(row, comment_idx)
            favorites= safe_int(row, fav_idx)

            status   = str(row[status_idx]).strip() if status_idx is not None and row[status_idx] else ''
            profile_visits = safe_int(row, profile_idx)
            follower_gain  = safe_int(row, follower_idx)
            videos.append({
                'title': title, 'pub_date': pub_date, 'status': status,
                'plays': plays, 'ctr5s': ctr5s, 'ctrcov': ctrcov,
                'bounce': bounce, 'duration': duration, 'finish': finish,
                'likes': likes, 'shares': shares,
                'comments': comments, 'favorites': favorites,
                'profile_visits': profile_visits, 'follower_gain': follower_gain,
            })

        log(f"解析到 {len(videos)} 条视频")
        return videos

    except Exception as e:
        log(f"❌ 解析 Excel 失败: {e}")
        import traceback; traceback.print_exc()
        return []


def save_to_db(videos):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_stats (
            timestamp DATETIME,
            title TEXT,
            publish_date TEXT,
            plays INTEGER,
            avg_duration_sec INTEGER,
            ctr REAL,
            finish_rate REAL,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            favorites INTEGER,
            danmaku INTEGER,
            status TEXT,
            profile_visits INTEGER,
            follower_gain INTEGER
        )
    """)
    # Add missing columns if table already exists
    for col in ['status', 'profile_visits', 'follower_gain']:
        try:
            cursor.execute(f"ALTER TABLE video_stats ADD COLUMN {col} {'TEXT' if col == 'status' else 'INTEGER'}")
        except:
            pass
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for v in videos:
        cursor.execute(
            "INSERT INTO video_stats (timestamp, title, publish_date, plays, avg_duration_sec, ctr, finish_rate, likes, comments, shares, favorites, danmaku, status, profile_visits, follower_gain) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, v['title'], v['pub_date'],
             v['plays'], int(v['duration']), v['ctr5s'], v.get('finish', 0),
             v['likes'], v['comments'], v['shares'],
             v['favorites'], 0, v.get('status', ''),
             v.get('profile_visits', 0), v.get('follower_gain', 0))
        )
    conn.commit()
    conn.close()
    return len(videos)


def build_report(videos, total_inserted, now_str):
    # 过滤掉私密/自见视频，只展示公开的最新3条
    public_videos = [v for v in videos if v.get('status', '') not in ('私密', '自见')]
    top3 = public_videos[:3]
    lines = [
        f"📊 <b>抖音数据更新</b>",
        f"🕐 {now_str}",
        f"✅ 共入库 <b>{total_inserted}</b> 条记录",
        "",
    ]
    for i, v in enumerate(top3, 1):
        title = v['title']
        if len(title) > 22:
            title = title[:22] + '…'
        lines.append(
            f"<b>{i}. {title}</b>\n"
            f"   📅 {v['pub_date']}\n"
            f"   ▶️ 播放 <b>{v['plays']:,}</b> | 封面点击 {v['ctrcov']}%\n"
            f"   ✅ 5s完播 {v['ctr5s']}% | 完播率 {v.get('finish', 'N/A')}% | 均时长 {v['duration']}s\n"
            f"   ❤️ 点赞 {v['likes']:,} | 💬 评论 {v['comments']:,} | 🔁 分享 {v['shares']:,}"
        )
        if i < len(top3):
            lines.append("")
    return "\n".join(lines)


def health_check():
    """采集前验证：AppleScript JS 是否开启、是否已登录。
    返回 (ok, reason)"""
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
        return False, "⏱️ AppleScript 执行超时"
    except Exception as e:
        err = str(e)
        if "Allow JavaScript" in err or "JavaScript 的功能已关闭" in err:
            return False, "🔧 AppleScript JS 已关闭 → Chrome 菜单栏：查看 → 开发者 → 勾选「允许 Apple 事件中的 JavaScript」"
        return False, f"⚠️ AppleScript 异常: {err[:120]}"
    
    if result.strip() == "no_tab":
        return False, "📄 未找到 creator.douyin.com 标签页"
    if "js_ok" not in result:
        return False, f"⚠️ JS 返回异常: {result[:100]}"
    
    return True, "ok"


def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log("=" * 50)
    log(f"🚀 开始抓取抖音数据 - {now_str}")
    log(f"HOME={_HOME} DOWNLOADS={DOWNLOADS_DIR} exists={os.path.exists(DOWNLOADS_DIR)}")

    # 0. 并发锁：防止手动运行和 cronjob 同时抢 Chrome
    lock_file = os.path.expanduser("~/.codex/douyin-tool/.douyin_scrape.lock")
    import fcntl
    lock_fd = open(lock_file, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("⚠️ 已有抓取进程在运行，跳过本次")
        send_telegram(f"⏭️ <b>跳过本次抓取</b>\n🕐 {now_str}\n上一个抓取进程尚未完成")
        return

    try:
        # 健康检查（2秒快速验证 AppleScript JS）
        ok, reason = health_check()
        if not ok:
            send_telegram(f"❌ <b>抖音数据抓取失败</b>\n🕐 {now_str}\n{reason}")
            return

        # 1. 刷新页面
        if not reload_page():
            send_telegram(f"❌ <b>抖音数据抓取失败</b>\n🕐 {now_str}\n未找到抖音创作者中心标签页")
            return

        # 2. 等待页面加载
        log("等待页面加载 (15秒)...")
        time.sleep(15)

        # 3. 切换到投稿列表
        if not switch_to_list_tab():
            log("⚠️ 切换投稿列表失败，重试...")
            time.sleep(3)
            switch_to_list_tab()

        # 4. 点击"刷新数据"按钮，确保数据最新
        refresh_js = """
(function(){
    var btns = Array.from(document.querySelectorAll('button'));
    var found = btns.filter(function(b){
        var t = (b.innerText || '').trim();
        // 刷(21047)新(26032)数(25968)据(25454)
        return t.charCodeAt(0)===21047 && t.charCodeAt(1)===26032 && t.charCodeAt(2)===25968 && t.charCodeAt(3)===25454;
    });
    if(found.length > 0) { found[0].click(); return 'refreshed'; }
    return 'refresh btn not found';
})()
"""
        tmp = "/tmp/dy_refresh.js"
        with open(tmp, "w") as f:
            f.write(refresh_js)
        r = run_js_file(tmp)
        log(f"点击刷新数据: {r}")
        time.sleep(4)  # 等数据刷新完成

        # 5. 轮询等待投稿列表的导出按钮出现（最多等30秒）
        log("等待投稿列表导出按钮出现...")
        before_ts = time.time() - 1  # 记录点击前的时间，全程只用这一个
        clicked = False
        for attempt in range(15):
            time.sleep(2)
            if click_export_button():
                clicked = True
                break
            log(f"  第{attempt+1}次未找到导出按钮，继续等待...")
        if not clicked:
            send_telegram(f"⚠️ <b>抖音数据抓取失败</b>\n🕐 {now_str}\n等待导出按钮超时")
            return

        xlsx_path = wait_for_download(before_ts)
        if not xlsx_path:
            # 下载超时，重试一次点击导出（before_ts 不变，确保能检测到之前已下载的文件）
            log("⚠️ 下载超时，重试点击导出按钮...")
            click_export_button()
            xlsx_path = wait_for_download(before_ts)
        if not xlsx_path:
            send_telegram(f"❌ <b>抖音数据抓取失败</b>\n🕐 {now_str}\n下载超时，请检查 Chrome 是否打开抖音创作者中心")
            return

        # 6. 解析数据
        videos = parse_xlsx(xlsx_path)
        if not videos:
            send_telegram(f"⚠️ <b>抖音数据解析失败</b>\n🕐 {now_str}\nExcel 解析失败")
            return

        # 7. 存入数据库
        inserted = save_to_db(videos)
        log(f"已入库 {inserted} 条")

        # 8. 删除临时文件
        try:
            os.remove(xlsx_path)
            log(f"已删除临时文件: {os.path.basename(xlsx_path)}")
        except:
            pass

        # 9. Telegram 汇报最新3条
        report = build_report(videos, inserted, now_str)
        send_telegram(report)

        # 10. 抓取总粉丝数据（导航到首页提取）
        try:
            follower_data = scrape_total_followers()
            if follower_data:
                save_account_stats(follower_data)
        except Exception as e:
            log(f"粉丝抓取跳过: {e}")

        # 11. 同步到 Notion
        try:
            import subprocess as _sp
            sync_result = _sp.run(
                    ['python3', os.path.expanduser('~/.codex/douyin-tool/scripts/notion_sync.py')],
                    timeout=300, capture_output=True, text=True, close_fds=True)
            if sync_result.returncode == 0:
                log("Notion 同步完成")
            else:
                detail = (sync_result.stderr or sync_result.stdout or "").strip()
                log(f"Notion 同步失败: exit={sync_result.returncode} {detail[:500]}")
        except Exception as e:
            log(f"Notion 同步跳过: {e}")

        log("✅ 完成")
    finally:
        # 释放锁
        lock_fd.close()


if __name__ == '__main__':
    main()
