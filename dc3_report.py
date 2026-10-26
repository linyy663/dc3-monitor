# -*- coding: utf-8 -*-
"""
地下城堡3 舆情监控（TapTap + 百度贴吧）
- 日报模式（默认）：抓取昨日数据
- 周报模式（--weekly）：抓取上周一~周日数据
- 生成 HTML 报告 + 飞书推送（interactive 卡片，格式与地下城堡4一致）
"""
import sys, os, json, time, re, uuid, urllib.parse
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
import requests

# ── Config ──────────────────────────────────────
APP_ID    = "221779"
GROUP_ID  = "308121"
GAME_NAME = "地下城堡3"
TIEBA_KW_URL = "https://tieba.baidu.com/mo/q/m?kw=%E5%9C%B0%E4%B8%8B%E5%9F%8E%E5%A0%A13&pn={pn}"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"

FEISHU_WEBHOOKS = [
    "https://open.feishu.cn/open-apis/bot/v2/hook/04ddffbe-60b0-4017-bb01-33a52e1d4b09",
]

WEEKLY = "--weekly" in sys.argv
NO_PUSH = "--no-push" in sys.argv
PUSH_ONLY = "--push-only" in sys.argv
today = datetime.now()

if WEEKLY:
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday  = last_monday + timedelta(days=6)
    START_DATE = last_monday.strftime("%Y-%m-%d")
    END_DATE   = last_sunday.strftime("%Y-%m-%d")
    DATE_LABEL = f"{START_DATE} ~ {END_DATE}"
    MODE_NAME  = "周报"
else:
    START_DATE = END_DATE = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    DATE_LABEL = START_DATE
    MODE_NAME  = "日报"

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data", "dc3")
HTML_DIR   = os.path.join(DATA_DIR, "html_report_daily" if not WEEKLY else "html_report_weekly")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(HTML_DIR, exist_ok=True)

JSON_FILE = os.path.join(DATA_DIR, f"dc3_{'week' if WEEKLY else 'daily'}_{START_DATE}_{END_DATE}.json")
PUSH_STATE_FILE = os.path.join(DATA_DIR, "push_state.json")
MODE_KEY = "weekly" if WEEKLY else "daily"
FORCE_PUSH = "--force" in sys.argv

CST = timezone(timedelta(hours=8))

def log(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)


# ── 防重复推送状态 ──────────────────────────────
def load_push_state():
    try:
        with open(PUSH_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def already_pushed():
    """该报告周期是否已成功推送过（防止补跑/重试造成重复推送）"""
    state = load_push_state()
    return state.get(MODE_KEY) == DATE_LABEL


def mark_pushed():
    state = load_push_state()
    state[MODE_KEY] = DATE_LABEL
    state[f"{MODE_KEY}_pushed_at"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(PUSH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[警告] 写入推送状态失败: {e}")


def shorten_url(long_url):
    try:
        api = f"https://is.gd/create.php?format=simple&url={urllib.parse.quote(long_url, safe='')}"
        r = requests.get(api, timeout=8)
        short = r.text.strip()
        if r.status_code == 200 and short.startswith("http"):
            return short
    except Exception as e:
        log(f"[URL缩短] 失败: {e}")
    return long_url

# ══════════════════════════════════════════════
# TapTap 数据抓取（评论/论坛/回复）
# ══════════════════════════════════════════════
def extract_rich_text(contents):
    if not contents: return ""
    if contents.get("text"): return contents.get("text", "")
    if contents.get("raw_text"): return contents.get("raw_text", "")
    json_arr = contents.get("json", [])
    if not json_arr: return ""
    parts = []
    for para in json_arr:
        for child in para.get("children", []):
            t = child.get("text", "")
            if t: parts.append(t)
    return "".join(parts)

def ts_to_date(ts):
    if isinstance(ts, (int, float)) and ts > 1_000_000_000:
        return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + 8 * 3600))
    return str(ts)[:10] if ts else ""

def ts_to_datetime(ts):
    if isinstance(ts, (int, float)) and ts > 1_000_000_000:
        return datetime.fromtimestamp(ts, tz=CST)
    return datetime.now(CST)

def extract_forum_item(item):
    m = item.get("moment", {})
    topic = m.get("topic", {})
    author = (m.get("author") or {}).get("user") or {}
    stat = m.get("stat") or {}
    title = topic.get("title", "")
    summary = topic.get("summary", "")
    content = summary if summary else extract_rich_text(m.get("contents", {}))
    if not title: title = content[:50].replace("\n", " ") if content else ""
    return {
        "source": "taptap_forum", "id": m.get("id_str", ""),
        "author": author.get("name", "匿名"),
        "date": ts_to_date(m.get("created_time", 0)),
        "datetime": ts_to_datetime(m.get("created_time", 0)).strftime("%Y-%m-%d %H:%M"),
        "title": title, "body": content, "text": content,
        "likes": stat.get("supports", 0), "shares": stat.get("share_count", 0),
        "views": stat.get("pv_total", 0),
        "comments_count": stat.get("comments", 0),
        "url": f"https://www.taptap.cn/moment/{m.get('id_str','')}",
        "comments": [], "category": "",
    }

def extract_review_item(item):
    m = item.get("moment", {})
    review = m.get("review", {})
    author = (m.get("author") or {}).get("user") or {}
    stat = m.get("stat") or {}
    content = extract_rich_text(review.get("contents", {}))
    return {
        "source": "taptap_review", "id": m.get("id_str", ""),
        "author": author.get("name", "匿名"),
        "date": ts_to_date(m.get("created_time", 0)),
        "datetime": ts_to_datetime(m.get("created_time", 0)).strftime("%Y-%m-%d %H:%M"),
        "title": "", "body": content, "text": content,
        "score": review.get("score", 0),
        "likes": stat.get("supports", 0), "views": stat.get("pv_total", 0),
        "url": "", "comments": [], "category": "",
    }

def extract_comment(comment):
    author = comment.get("author") or {}
    author_name = author.get("name") or (author.get("user") or {}).get("name") or "匿名"
    text = extract_rich_text(comment.get("contents", {}))
    nested = []
    for cp in comment.get("child_posts", []):
        cp_author = (cp.get("author") or {}).get("name") or "?"
        cp_text = extract_rich_text(cp.get("contents", {}))
        if cp_text: nested.append({"author": cp_author, "text": cp_text})
    return {
        "id": str(comment.get("id_str") or comment.get("id", "")),
        "author": author_name, "text": text,
        "time": ts_to_datetime(comment.get("created_time", 0)).strftime("%Y-%m-%d %H:%M"),
        "likes": comment.get("like_count") or comment.get("ups", 0),
        "nested_replies": nested,
    }

def _taptap_session():
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json", "Referer": f"https://www.taptap.cn/app/{APP_ID}",
    })
    try: sess.get(f"https://www.taptap.cn/app/{APP_ID}", timeout=15)
    except: pass
    xua = urllib.parse.quote(f"V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android&UID={uuid.uuid4()}&OS=Windows&OSV=10&DT=PC")
    return sess, xua

def fetch_reviews():
    log(f"[1/5] 抓取 TapTap 评分评论 ({START_DATE} ~ {END_DATE})...")
    sess, xua = _taptap_session()
    all_reviews, from_offset, found_older, fail_streak = [], 0, False, 0
    max_offset = 500 if not WEEKLY else 1500
    while from_offset < max_offset:
        url = f"https://www.taptap.cn/webapiv2/review/v2/list-by-app?app_id={APP_ID}&sort=new&limit=10&from={from_offset}&X-UA={xua}"
        try:
            r = sess.get(url, timeout=20)
            if "text/html" in r.headers.get("Content-Type","") or r.text[:100].lower().startswith("<!doctype"):
                sess.cookies.clear()
                try: sess.get(f"https://www.taptap.cn/app/{APP_ID}", timeout=15)
                except: pass
                fail_streak += 1; time.sleep(3)
                if fail_streak >= 5: break
                continue
            d = r.json(); fail_streak = 0
        except Exception:
            fail_streak += 1
            if fail_streak >= 5: break
            time.sleep(2); continue
        items = d.get("data",{}).get("list",[])
        if not items: break
        for item in items:
            ri = extract_review_item(item)
            if START_DATE <= ri["date"] <= END_DATE: all_reviews.append(ri)
            elif ri["date"] < START_DATE: found_older = True
        if found_older and from_offset >= 50: break
        from_offset += 10; time.sleep(0.8)
    log(f"  [评论] 共 {len(all_reviews)} 条")
    return all_reviews

JS_FETCH = "async (url) => { try { const r = await fetch(url, {credentials:'include'}); const t = await r.text(); return {ok:r.ok, status:r.status, body:t}; } catch(e) { return {ok:false, status:0, body:e.toString()}; } }"

def fetch_forum():
    log(f"[2/5] 抓取 TapTap 论坛帖子 ({START_DATE} ~ {END_DATE})...")
    sess, xua = _taptap_session()
    test_url = f"https://www.taptap.cn/webapiv2/feed/v7/by-group?from=0&group_id={GROUP_ID}&limit=1&sort=created&status=0&type=feed&with_hot_comment=true&X-UA={xua}"
    use_requests = False
    try:
        tr = sess.get(test_url, timeout=15)
        if tr.status_code == 200 and '"type":"moment"' in tr.text:
            use_requests = True; log("  [论坛] requests 可用")
        else:
            log("  [论坛] requests 不可用，切换到 Playwright")
    except:
        log("  [论坛] requests 异常，切换到 Playwright")

    all_posts, found_older, fail_streak = [], False, 0
    from_offset = 0
    max_offset = 1000 if not WEEKLY else 3000

    def _process_items(items):
        nonlocal found_older
        for item in items:
            fi = extract_forum_item(item)
            if START_DATE <= fi["date"] <= END_DATE: all_posts.append(fi)
            elif fi["date"] < START_DATE: found_older = True

    if use_requests:
        while from_offset < max_offset:
            url = f"https://www.taptap.cn/webapiv2/feed/v7/by-group?from={from_offset}&group_id={GROUP_ID}&limit=10&sort=created&status=0&type=feed&with_hot_comment=true&X-UA={xua}"
            try:
                r = sess.get(url, timeout=20)
                if "text/html" in r.headers.get("Content-Type","") or r.text[:100].lower().startswith("<!doctype"):
                    fail_streak += 1; time.sleep(2)
                    if fail_streak >= 5: break
                    continue
                d = r.json(); fail_streak = 0
            except:
                fail_streak += 1; time.sleep(2)
                if fail_streak >= 5: break
                continue
            items = d.get("data",{}).get("list",[])
            if not items: break
            _process_items(items)
            pn = from_offset//10+1
            log(f"  [论坛] p{pn}: {len(items)}条, 累计匹配 {len(all_posts)}")
            if found_older and from_offset >= (100 if not WEEKLY else 300): break
            from_offset += 10; time.sleep(0.8)
    else:
        try: from playwright.sync_api import sync_playwright
        except ImportError:
            log("  [论坛] playwright 未安装"); return []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36", locale="zh-CN")
            page = ctx.new_page(); page.set_default_timeout(15000)
            try: page.goto(f"https://www.taptap.cn/app/{APP_ID}", wait_until="commit", timeout=20000)
            except: pass
            time.sleep(2)
            while from_offset < max_offset:
                url = f"https://www.taptap.cn/webapiv2/feed/v7/by-group?from={from_offset}&group_id={GROUP_ID}&limit=10&sort=created&status=0&type=feed&with_hot_comment=true&X-UA={xua}"
                try:
                    result = page.evaluate(JS_FETCH, url)
                except:
                    fail_streak += 1; time.sleep(2)
                    if fail_streak >= 5: break; continue
                if not result or not result.get("ok"):
                    fail_streak += 1; from_offset += 10; time.sleep(1)
                    if fail_streak >= 5: break; continue
                fail_streak = 0
                try: d = json.loads(result["body"])
                except: from_offset += 10; continue
                items = d.get("data",{}).get("list",[])
                if not items: break
                _process_items(items)
                pn = from_offset//10+1
                log(f"  [论坛] p{pn}: {len(items)}条, 累计匹配 {len(all_posts)}")
                if found_older and from_offset >= (100 if not WEEKLY else 300): break
                from_offset += 10; time.sleep(0.3)
            browser.close()
    log(f"  [论坛] 共 {len(all_posts)} 条")
    return all_posts

def fetch_comments_requests(all_posts):
    if not all_posts: return 0, 0
    log(f"[3/5] 抓取 {len(all_posts)} 个帖子的回复...")
    sess, xua = _taptap_session()
    total_c = total_n = 0
    for i, post in enumerate(all_posts):
        url = f"https://www.taptap.cn/webapiv2/moment-comment/v1/by-moment?moment_id={post['id']}&from=0&limit=20&X-UA={xua}"
        try:
            r = sess.get(url, timeout=20)
            comments_raw = r.json().get("data",{}).get("list",[])
        except: comments_raw = []
        comments = [extract_comment(c) for c in comments_raw]
        post["comments"] = comments
        nc = len(comments); nn = sum(len(c.get("nested_replies",[])) for c in comments)
        total_c += nc; total_n += nn
        if (i+1) % 20 == 0: log(f"  [回复] {i+1}/{len(all_posts)}  (累计: {total_c}回复, {total_n}嵌套)")
        time.sleep(0.2)
    log(f"  [回复] 完成: {total_c} 条回复, {total_n} 条嵌套")
    return total_c, total_n

# ══════════════════════════════════════════════
# 贴吧数据抓取（移动端 + Playwright）
# ══════════════════════════════════════════════
def parse_tieba_time(s):
    now = datetime.now()
    s = s.strip()
    m = re.match(r"今天\s*(\d{1,2}):(\d{2})", s)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    m = re.match(r"昨天\s*(\d{1,2}):(\d{2})", s)
    if m:
        d = now - timedelta(days=1)
        return d.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    m = re.match(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", s)
    if m:
        return datetime(now.year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    m = re.match(r"(\d{1,2})-(\d{1,2})$", s)
    if m:
        return datetime(now.year, int(m.group(1)), int(m.group(2)))
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{1,2}):(\d{2})$", s)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    return None

def extract_mobile_threads(page):
    return page.evaluate('''() => {
        const items = [];
        document.querySelectorAll('li.tl_shadow').forEach(li => {
            const link = li.querySelector('a.ti_item');
            if (!link) return;
            const href = link.getAttribute('href') || '';
            const m = href.match(/\\/p\\/(\\d+)/);
            if (!m) return;
            const authorEl = li.querySelector('.ti_author');
            const timeEl = li.querySelector('.ti_time');
            const titleEl = li.querySelector('.ti_title');
            const replyEl = li.querySelector('.btn_reply .btn_icon');
            items.push({
                tid: m[1],
                author: authorEl ? authorEl.textContent.trim() : '',
                time: timeEl ? timeEl.textContent.trim() : '',
                title: titleEl ? titleEl.textContent.trim() : '',
                replies: replyEl ? replyEl.textContent.trim() : '0'
            });
        });
        return items;
    }''')

def fetch_tieba():
    log(f"[4/5] 抓取百度贴吧帖子 ({START_DATE} ~ {END_DATE})...")
    try:
        from curl_cffi import requests as curl_requests
        session = curl_requests.Session()
        session.impersonate = "chrome120"
        session.get("https://www.baidu.com/", headers={"Accept": "text/html", "Accept-Language": "zh-CN,zh;q=0.9"})
        session.get("https://tieba.baidu.com/", headers={"Accept": "text/html"})
        cookies = [{"name": n, "value": v, "domain": ".baidu.com", "path": "/"}
                   for n, v in session.cookies.items()]
    except Exception as e:
        log(f"  [贴吧] cookie 获取失败: {e}")
        cookies = []

    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt   = datetime.strptime(END_DATE, "%Y-%m-%d") + timedelta(days=1)
    all_threads = {}
    posts = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 375, "height": 812},
                locale="zh-CN",
                user_agent=MOBILE_UA,
            )
            if cookies: context.add_cookies(cookies)
            page = context.new_page()
            max_pages = 3 if not WEEKLY else 8
            for pn_idx in range(max_pages):
                pn = pn_idx * 30
                url = TIEBA_KW_URL.format(pn=pn)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                items = extract_mobile_threads(page)
                new_count, older_count = 0, 0
                for item in items:
                    tid = item["tid"]
                    if tid in all_threads:
                        continue
                    all_threads[tid] = item
                    new_count += 1
                    title = item.get("title", "").strip()
                    if not title or "置顶" in title: continue
                    post_dt = parse_tieba_time(item.get("time", ""))
                    if not post_dt: continue
                    if post_dt < start_dt:
                        older_count += 1
                        continue
                    if post_dt >= end_dt: continue
                    reply_str = item.get("replies", "0").strip()
                    posts.append({
                        "source": "tieba", "id": tid,
                        "author": item.get("author", "").strip() or "未知",
                        "date": post_dt.strftime("%Y-%m-%d"),
                        "datetime": post_dt.strftime("%Y-%m-%d %H:%M"),
                        "title": title[:100], "body": title, "text": title,
                        "likes": 0, "shares": 0, "views": 0,
                        "comments_count": int(reply_str) if reply_str.isdigit() else 0,
                        "url": f"https://tieba.baidu.com/p/{tid}",
                        "comments": [], "category": "",
                    })
                log(f"  [贴吧] 第{pn_idx+1}页: 提取{len(items)}条, 新增{new_count}, 范围内累计{len(posts)}")
                if older_count >= len(items) - 1 and pn_idx >= 1:
                    log("  [贴吧] 已翻到 older 数据，停止翻页")
                    break
            browser.close()
    except ImportError:
        log("  [贴吧] playwright 未安装")
    except Exception as e:
        log(f"  [贴吧] 抓取异常: {e}")
    log(f"  [贴吧] 共 {len(posts)} 条")
    return posts

# ══════════════════════════════════════════════
# 分类
# ══════════════════════════════════════════════
def classify_content(item):
    title = item.get("title", "") or ""
    body = item.get("body", "") or item.get("text", "") or ""
    full_text = f"{title} {body}".lower()
    for c in item.get("comments", []):
        full_text += " " + (c.get("text", "") or "")
        for nr in c.get("nested_replies", []):
            full_text += " " + (nr.get("text", "") or "")

    bug_kw = ["bug","闪退","卡死","黑屏","崩溃","报错","错误","卡住","加载不了","掉线","延迟",
              "卡顿","没反应","出不来","打不开","异常","乱码","消失","不显示","吞了","失效",
              "无法","进不去","服务器","维护","连接失败","登录不了","回档","数据丢失",
              "装备异常","装备丢失","属性异常","不生效","无效","没触发","不能","没了","无法使用",
              "上不去","连不上","登不上"]
    suggest_kw = ["建议","希望","能不能","求","期待","优化","改善","改进","增加","添加","加入",
                  "更新","调整","平衡","修改","改一下","活动","福利","奖励","玩法","功能",
                  "要是","如果","出个","来个","开放","保底","概率","透明","公示","说明",
                  "加点","降低","提高","加强","削弱","可玩性","引导","提示","新手","难度"]
    complain_kw = ["垃圾","坑","骗","太贵","不值","差评","失望","后悔","恶心","离谱","无语",
                   "不好玩","无聊","没意思","浪费时间","弃坑","卸载","不玩了","退游","关服",
                   "换皮","抄袭","逼氪","吃相","难看","割韭菜","氪金","付费","坑钱","骗氪",
                   "逼肝","服了","辣鸡","坑爹","劝退","凉了"]
    guide_kw = ["攻略","阵容","打法","过关","配置","阵容推荐","怎么打","怎么过","求阵容",
                "求助","请教","大佬","指点","帮忙看看","分享","思路","带什么","配什么",
                "用什么","选哪个","怎么配","怎么选","哪个好","谁厉害","通关","出装",
                "装备搭配","技能","加点","流派","过图","教程","技巧","心得","推荐"]
    official_kw = ["公告","官方","可公开","更新预告","维护预告","活动预告"]

    s_s = sum(1 for kw in suggest_kw if kw in full_text)
    s_b = sum(1 for kw in bug_kw if kw in full_text)
    s_c = sum(1 for kw in complain_kw if kw in full_text)
    s_g = sum(1 for kw in guide_kw if kw in full_text)
    s_o = sum(1 for kw in official_kw if kw in full_text)

    scores = {"建议": s_s, "BUG": s_b, "吐槽": s_c, "攻略交流": s_g, "官方": s_o}
    max_cat = max(scores, key=scores.get)
    if scores[max_cat] == 0: return "其他"
    if s_o >= 1: return "官方"
    if s_g >= 2: return "攻略交流"
    return max_cat

# ══════════════════════════════════════════════
# HTML & 飞书摘要（格式与地下城堡4一致）
# ══════════════════════════════════════════════
CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f7fa;color:#333;line-height:1.7}
.container{max-width:900px;margin:0 auto;padding:20px}
.header{background:linear-gradient(135deg,#0f3460,#16213e);color:#fff;padding:40px 30px;border-radius:12px;margin-bottom:24px}
.header h1{font-size:26px;margin-bottom:8px}
.header .meta{opacity:.8;font-size:14px}
.card{background:#fff;border-radius:10px;padding:24px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card h2{font-size:18px;color:#1a1a2e;border-bottom:2px solid #e8ecf0;padding-bottom:10px;margin-bottom:16px}
.card h3{font-size:15px;color:#444;margin:12px 0 8px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:16px}
.stat-box{background:#f0f4f8;border-radius:8px;padding:14px;text-align:center}
.stat-box .num{font-size:28px;font-weight:700;color:#1a1a2e}
.stat-box .label{font-size:12px;color:#888;margin-top:4px}
table{width:100%;border-collapse:collapse;margin:12px 0}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #e8ecf0;font-size:14px}
th{background:#f0f4f8;font-weight:600;color:#555}
tr:hover{background:#fafbfc}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;margin:0 2px}
.tag-bug{background:#fee;color:#c0392b}
.tag-suggest{background:#e8f4fd;color:#2980b9}
.tag-complain{background:#fff3e0;color:#e67e22}
.tag-guide{background:#e8f8e8;color:#27ae60}
.tag-official{background:#f0e6ff;color:#8e44ad}
.tag-other{background:#f5f5f5;color:#888}
.item-row{padding:10px 0;border-bottom:1px dotted #e8ecf0}
.item-row:last-child{border-bottom:none}
.item-title{font-weight:600;color:#1a1a2e}
.item-title a{color:#1a73e8;text-decoration:none}
.item-title a:hover{text-decoration:underline}
.item-meta{font-size:12px;color:#999;margin:4px 0}
.bar-chart{display:flex;align-items:center;margin:4px 0}
.bar-label{width:70px;font-size:13px;text-align:right;padding-right:8px}
.bar-track{flex:1;height:22px;background:#f0f4f8;border-radius:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:4px;display:flex;align-items:center;padding-left:8px;font-size:12px;font-weight:600;color:#fff;min-width:30px}
.bar-suggest{background:linear-gradient(90deg,#3498db,#2980b9)}
.bar-bug{background:linear-gradient(90deg,#e74c3c,#c0392b)}
.bar-complain{background:linear-gradient(90deg,#f39c12,#e67e22)}
.bar-guide{background:linear-gradient(90deg,#2ecc71,#27ae60)}
.bar-official{background:linear-gradient(90deg,#9b59b6,#8e44ad)}
.bar-other{background:linear-gradient(90deg,#95a5a6,#7f8c8d)}
.summary-box{background:#fffdf0;border:1px solid #f0e6a0;border-radius:8px;padding:16px;margin-bottom:16px;font-size:14px}
.summary-box h2{color:#b8860b;border-color:#f0e6a0}
.priority-item{padding:8px 0;border-bottom:1px dashed #e8ecf0}
.priority-item:last-child{border-bottom:none}
.p0{color:#c0392b;font-weight:700}
.p1{color:#e67e22;font-weight:700}
.p2{color:#7f8c8d;font-weight:700}
.src-badge{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;margin-right:4px}
.src-taptap{background:#e3f2fd;color:#1565c0}
.src-tieba{background:#fff3e0;color:#e65100}
.footer{text-align:center;color:#aaa;font-size:12px;margin-top:24px;padding:16px}
a{color:#1a73e8;text-decoration:none}
a:hover{text-decoration:underline}
"""

def summarize_items(items):
    if not items: return ""
    lines = []
    for i, item in enumerate(items[:5]):
        title = (item.get("title", "") or item.get("body", "")[:30] or "无标题")[:40]
        body = (item.get("body", "") or item.get("text", "") or "")
        snippet = body[:150].replace("\n", " ").strip()
        if not snippet: snippet = title
        lines.append(f"{i+1}. **{title}**：{snippet}")
    return "\n".join(lines)

def src_badge(source):
    if source == "tieba": return '<span class="src-badge src-tieba">贴吧</span>'
    return '<span class="src-badge src-taptap">TapTap</span>'

def generate_html_report(all_items, date_label, mode_name):
    reviews = [i for i in all_items if i["source"] == "taptap_review"]
    posts   = [i for i in all_items if i["source"] == "taptap_forum"]
    tieba_posts = [i for i in all_items if i["source"] == "tieba"]
    total = len(all_items)
    cat_map = defaultdict(list)
    for item in all_items: cat_map[item["category"]].append(item)
    total_c = sum(len(i.get("comments",[])) for i in all_items)
    total_n = sum(sum(len(c.get("nested_replies",[])) for c in i.get("comments",[])) for i in all_items)
    tieba_replies = sum(p.get("comments_count", 0) for p in tieba_posts)
    total_interact = total + total_c + total_n + tieba_replies

    H = []
    H.append(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{GAME_NAME} 舆情{mode_name} {date_label}</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
<div class="header" style="background:linear-gradient(135deg,#0f3460,#16213e)">
<h1>{GAME_NAME} 舆情{mode_name}（TapTap + 贴吧）</h1>
<div class="meta">📅 {date_label} &nbsp;|&nbsp; 🕐 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp; 📡 数据来源: TapTap + 百度贴吧</div>
</div>""")

    cat_order = ["建议","BUG","吐槽","攻略交流","官方","其他"]
    bug_items = cat_map.get("BUG", [])
    suggest_items = cat_map.get("建议", [])
    complain_items = cat_map.get("吐槽", [])

    H.append(f'<div class="summary-box"><h2>📋 本{ "日" if mode_name=="日报" else "周" }舆情概括</h2>')
    cat_parts = []
    for i, cat in enumerate(cat_order):
        cnt = len(cat_map.get(cat, []))
        if cnt > 0: cat_parts.append(f'<span class="tag tag-{["suggest","bug","complain","guide","official","other"][i]}">{cat} {cnt}条</span>')
    H.append(f'<p>本{"日" if mode_name=="日报" else "周"}共产生 <strong>{total}</strong> 条内容（TapTap：{len(reviews)}条评分 + {len(posts)}条论坛帖子；贴吧：{len(tieba_posts)}条帖子），玩家回复 <strong>{total_c}</strong> 条，嵌套回复 <strong>{total_n}</strong> 条，贴吧回复 <strong>{tieba_replies}</strong> 条，互动总量 <strong>{total_interact}</strong> 条。分类分布：{" ".join(cat_parts)}</p>')

    if suggest_items:
        H.append(f'<p><strong>💡 建议聚焦：</strong>共 {len(suggest_items)} 条建议。</p>')
    if bug_items:
        H.append(f'<p><strong>🐛 BUG聚焦：</strong>报告了 <strong>{len(bug_items)}</strong> 个问题：</p>')
        for line in summarize_items(bug_items).split("\n"):
            H.append(f'<p style="margin:2px 0 2px 16px;font-size:13px">{line}</p>')
    if complain_items:
        H.append(f'<p><strong>😤 吐槽聚焦：</strong>共 {len(complain_items)} 条负面内容：</p>')
        for line in summarize_items(complain_items).split("\n"):
            H.append(f'<p style="margin:2px 0 2px 16px;font-size:13px">{line}</p>')

    priority_parts = []
    if bug_items: priority_parts.append(f'<strong>P0</strong> 修复 {len(bug_items)} 个BUG')
    if suggest_items: priority_parts.append(f'<strong>P1</strong> 评估 {len(suggest_items)} 条建议')
    if complain_items: priority_parts.append(f'<strong>P2</strong> 关注 {len(complain_items)} 条吐槽')
    if priority_parts:
        H.append(f'<p><strong>📌 优先级：</strong>{"；".join(priority_parts)}</p>')
    H.append('</div>')

    H.append(f"""<div class="stats-grid">
<div class="stat-box"><div class="num">{total}</div><div class="label">内容总数</div></div>
<div class="stat-box"><div class="num">{len(posts)}</div><div class="label">TapTap帖子</div></div>
<div class="stat-box"><div class="num">{len(reviews)}</div><div class="label">TapTap评分</div></div>
<div class="stat-box"><div class="num">{len(tieba_posts)}</div><div class="label">贴吧帖子</div></div>
<div class="stat-box"><div class="num">{total_c + tieba_replies}</div><div class="label">帖子回复</div></div>
<div class="stat-box"><div class="num">{total_n}</div><div class="label">嵌套回复</div></div>
<div class="stat-box"><div class="num">{total_interact}</div><div class="label">互动总量</div></div>
</div>""")

    H.append('<div class="card"><h2>📊 分类分布</h2>')
    max_cat_cnt = max([len(cat_map.get(c,[])) for c in cat_order] or [1]) or 1
    bar_colors = {"建议":"bar-suggest","BUG":"bar-bug","吐槽":"bar-complain","攻略交流":"bar-guide","官方":"bar-official","其他":"bar-other"}
    for cat in cat_order:
        cnt = len(cat_map.get(cat,[]))
        pct = cnt/max_cat_cnt*100
        H.append(f'<div class="bar-chart"><div class="bar-label">{cat}</div><div class="bar-track"><div class="bar-fill {bar_colors.get(cat,"")}" style="width:{pct:.0f}%">{cnt}条</div></div></div>')
    H.append('</div>')

    # TapTap hot posts TOP10
    hot_posts = sorted([p for p in posts if p.get("comments")], key=lambda x: len(x["comments"]), reverse=True)[:10]
    if hot_posts:
        H.append('<div class="card"><h2>⭐ TapTap 热议帖子 TOP10</h2><table><tr><th>#</th><th>标题</th><th>作者</th><th>回复</th></tr>')
        for idx, p in enumerate(hot_posts):
            title = (p.get("title","") or p.get("body","")[:40] or "（无标题）")[:50]
            H.append(f'<tr><td>{idx+1}</td><td><a href="{p["url"]}" target="_blank">{title}</a></td><td>{p["author"]}</td><td>{len(p["comments"])}</td></tr>')
        H.append('</table>')
        for idx, p in enumerate(hot_posts[:3]):
            title = (p.get("title","") or p.get("body","")[:30])[:40]
            H.append(f'<h3>TOP{idx+1}: <a href="{p["url"]}" target="_blank">{title}</a></h3>')
            H.append(f'<div class="item-meta">👤 {p["author"]}  |  💬 {len(p["comments"])}回复</div>')
            for c in p.get("comments", [])[:3]:
                H.append(f'<div class="comment-thread" style="margin:6px 0 6px 16px;padding:6px 0 6px 12px;border-left:2px solid #e0e6ed"><div style="font-weight:600;font-size:13px">{c["author"]}</div><div style="font-size:13px;color:#666;margin:2px 0">{c["text"][:200]}</div></div>')
        H.append('</div>')

    # Tieba posts
    if tieba_posts:
        H.append(f'<div class="card"><h2>🔥 贴吧热议帖子 ({len(tieba_posts)}条)</h2>')
        for idx, p in enumerate(sorted(tieba_posts, key=lambda x: -x.get("comments_count",0)), 1):
            cat = p.get("category","其他")
            cat_class = {"建议":"tag-suggest","BUG":"tag-bug","吐槽":"tag-complain","攻略交流":"tag-guide","官方":"tag-official"}.get(cat,"tag-other")
            H.append(f'<div class="item-row"><span class="tag {cat_class}">{cat}</span> <span class="item-title"><a href="{p["url"]}" target="_blank">{idx}. {p["title"]}</a></span> <span class="item-meta">{src_badge(p["source"])} 👤 {p["author"]}  💬 {p.get("comments_count",0)}回复</span></div>')
        H.append('</div>')

    # All TapTap posts
    if posts:
        H.append(f'<div class="card"><h2>📋 本{"日" if mode_name=="日报" else "周"}全部 TapTap 论坛帖子 ({len(posts)}条)</h2>')
        for idx, p in enumerate(posts, 1):
            title = (p.get("title","") or p.get("body","")[:50] or "（无标题）")[:60]
            cat = p.get("category","其他")
            cat_class = {"建议":"tag-suggest","BUG":"tag-bug","吐槽":"tag-complain","攻略交流":"tag-guide","官方":"tag-official"}.get(cat,"tag-other")
            H.append(f'<div class="item-row"><span class="tag {cat_class}">{cat}</span> <span class="item-title"><a href="{p["url"]}" target="_blank">{idx}. {title}</a></span> <span class="item-meta">👤 {p["author"]}  💬 {len(p.get("comments",[]))}回复</span></div>')
        H.append('</div>')

    H.append(f'<div class="footer">日报由地下城堡3舆情监控系统自动生成（TapTap + 贴吧） | {date_label} | {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>')
    H.append('</div></body></html>')
    return "\n".join(H)

def generate_chat_summary(all_items, date_label, mode_name, report_url=""):
    reviews = [i for i in all_items if i["source"] == "taptap_review"]
    posts   = [i for i in all_items if i["source"] == "taptap_forum"]
    tieba_posts = [i for i in all_items if i["source"] == "tieba"]
    total = len(all_items)
    cat_map = defaultdict(list)
    for item in all_items: cat_map[item["category"]].append(item)
    total_c = sum(len(i.get("comments",[])) for i in all_items)
    total_n = sum(sum(len(c.get("nested_replies",[])) for c in i.get("comments",[])) for i in all_items)
    tieba_replies = sum(p.get("comments_count", 0) for p in tieba_posts)
    total_interact = total + total_c + total_n + tieba_replies

    bug_items = cat_map.get("BUG", [])
    suggest_items = cat_map.get("建议", [])
    complain_items = cat_map.get("吐槽", [])

    period = "日" if mode_name == "日报" else "周"
    L = []
    L.append(f"📊 **{GAME_NAME} 舆情{mode_name}（TapTap + 贴吧）**")
    L.append(f"📅 {date_label}")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"📋 **本{period}舆情概括**")
    L.append("")
    L.append(f"本{period}共产生 **{total}** 条内容（TapTap：{len(reviews)}条评分 + {len(posts)}条论坛帖子；贴吧：{len(tieba_posts)}条帖子），玩家回复 **{total_c + tieba_replies}** 条，嵌套回复 **{total_n}** 条，互动总量 **{total_interact}** 条。")
    L.append("")

    cat_order = ["建议","BUG","吐槽","攻略交流","官方","其他"]
    cat_parts = [f"{cat} {len(cat_map.get(cat,[]))}条" for cat in cat_order if len(cat_map.get(cat,[])) > 0]
    L.append(f"📊 **分类分布**：{'  '.join(cat_parts)}")
    L.append("")

    if suggest_items:
        L.append(f"💡 **建议聚焦**：共 {len(suggest_items)} 条。")
        L.append("")
    if bug_items:
        L.append(f"🐛 **BUG聚焦**：报告了 **{len(bug_items)}** 个问题：")
        L.append("")
        L.append(summarize_items(bug_items))
        L.append("")
    if complain_items:
        L.append(f"😤 **吐槽聚焦**：共 {len(complain_items)} 条负面内容：")
        L.append("")
        L.append(summarize_items(complain_items))
        L.append("")

    # TOP10：TapTap + 贴吧 混合按互动排序
    mixed = []
    for p in posts:
        mixed.append((p, len(p.get("comments", []))))
    for p in tieba_posts:
        mixed.append((p, p.get("comments_count", 0)))
    mixed.sort(key=lambda x: x[1], reverse=True)
    top10 = [m for m in mixed if m[1] > 0][:10]
    if top10:
        L.append("---")
        L.append("")
        L.append("⭐ **热议帖子 TOP10（TapTap + 贴吧）**")
        L.append("")
        for idx, (p, rc) in enumerate(top10):
            title = (p.get("title","") or p.get("body","")[:40] or "（无标题）")[:40]
            src = "贴吧" if p["source"] == "tieba" else "TapTap"
            short_url = shorten_url(p.get("url",""))
            L.append(f"{idx+1}. [{title}]({short_url}) — [{src}] {p['author']} | {rc}回复")
        L.append("")

        L.append("---")
        L.append("")
        for idx, (p, rc) in enumerate(top10[:3]):
            title = (p.get("title","") or p.get("body","")[:30])[:30]
            L.append(f"🔥 **TOP{idx+1}：{title}**")
            L.append("")
            if p.get("comments"):
                for c in p.get("comments", [])[:2]:
                    L.append(f"> {c['author']}：{c['text'][:120]}")
            else:
                L.append(f"> 原帖：{(p.get('text','') or p.get('title',''))[:120]}")
            L.append("")

    L.append("---")
    L.append("")
    L.append(f"📄 **完整{mode_name}**：{report_url or '即将部署'}")

    return "\n".join(L)

# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def push_feishu_card(chat_text):
    """推送 interactive 卡片到所有飞书群"""
    import urllib.request
    payload = json.dumps({
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": f"{GAME_NAME} 舆情{MODE_NAME}（TapTap+贴吧）"}, "template": "blue"},
            "elements": [{"tag": "markdown", "content": chat_text}]
        }
    }).encode("utf-8")
    ok_count = 0
    for hook in FEISHU_WEBHOOKS:
        req = urllib.request.Request(hook, data=payload, headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read().decode("utf-8"))
            ok = result.get("code") == 0 or result.get("StatusCode") == 0
            if ok:
                ok_count += 1
            log(f"[飞书推送] {'OK' if ok else 'FAIL'} ({hook[-12:]}): {result}")
        except Exception as e:
            log(f"[飞书推送] ERROR ({hook[-12:]}): {e}")

    if ok_count == len(FEISHU_WEBHOOKS) and ok_count > 0:
        mark_pushed()
        log(f"[推送状态] 已记录 {MODE_KEY} {DATE_LABEL} 推送成功，同日重复运行将被拦截")
    else:
        log(f"[推送状态] 推送未全部成功（{ok_count}/{len(FEISHU_WEBHOOKS)}），不记录状态，允许重试")
    return ok_count


def skip_if_pushed():
    """已推送过则跳过；--force 可强制重推"""
    if already_pushed() and not FORCE_PUSH:
        log("=" * 60)
        log(f"[跳过推送] {MODE_NAME} {DATE_LABEL} 已成功推送过，本次不重复推送")
        log("[跳过推送] 如需强制重推，请加 --force 参数")
        log("=" * 60)
        return True
    if already_pushed() and FORCE_PUSH:
        log(f"[强制推送] {MODE_NAME} {DATE_LABEL} 此前已推送，因指定 --force 仍将重推")
    return False


def push_only_mode():
    """仅推送模式：读取已生成的摘要文件，替换部署链接占位符后推送（部署完成后调用）"""
    report_url = ""
    if "--report-url" in sys.argv:
        idx = sys.argv.index("--report-url")
        if idx + 1 < len(sys.argv):
            report_url = sys.argv[idx + 1]

    summary_file = os.path.join(DATA_DIR, f"chat_summary_{'weekly' if WEEKLY else 'daily'}_latest.txt")
    log("=" * 60)
    log(f"{GAME_NAME} 舆情{MODE_NAME} 仅推送模式（部署后推送）")
    log("=" * 60)

    if not os.path.exists(summary_file):
        log(f"[错误] 找不到摘要文件 {summary_file}，请先运行生成流程（不带 --push-only）")
        return

    if skip_if_pushed():
        return

    with open(summary_file, encoding="utf-8") as f:
        chat_text = f.read()

    if report_url:
        chat_text = chat_text.replace("__CLOUDSTUDIO_URL__", report_url)
        log(f"[推送] 使用部署链接: {report_url}")
    else:
        log("[警告] 未提供 --report-url，卡片中的完整日报链接将显示占位文字")

    push_feishu_card(chat_text)
    log("=" * 60)


def main():
    if PUSH_ONLY:
        push_only_mode()
        return

    log("=" * 60)
    log(f"{GAME_NAME} 舆情{MODE_NAME}（TapTap + 贴吧）")
    log(f"日期: {DATE_LABEL}")
    log("=" * 60)

    reviews = fetch_reviews()
    posts = fetch_forum()
    total_c, total_n = fetch_comments_requests(posts)
    tieba_posts = fetch_tieba()

    all_items = reviews + posts + tieba_posts
    for item in all_items:
        item["category"] = classify_content(item)
    cat_count = Counter(i["category"] for i in all_items)
    log(f"\n分类结果: {dict(cat_count)}")

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)
    log(f"[保存] JSON -> {os.path.basename(JSON_FILE)} ({len(all_items)} items)")

    log("[生成] HTML 报告...")
    html_report = generate_html_report(all_items, DATE_LABEL, MODE_NAME)
    html_path = os.path.join(HTML_DIR, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_report)
    log(f"[保存] HTML -> {html_path} ({len(html_report)} chars)")

    summary_file = os.path.join(DATA_DIR, f"chat_summary_{'weekly' if WEEKLY else 'daily'}_latest.txt")
    report_url = ""
    if "--report-url" in sys.argv:
        idx = sys.argv.index("--report-url")
        if idx + 1 < len(sys.argv):
            report_url = sys.argv[idx + 1]
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(generate_chat_summary(all_items, DATE_LABEL, MODE_NAME, report_url=report_url or "__CLOUDSTUDIO_URL__"))
    log(f"[保存] 推送摘要 -> {os.path.basename(summary_file)}")

    log("=" * 60)
    tieba_replies = sum(p.get("comments_count", 0) for p in tieba_posts)
    log(f"汇总: {len(reviews)}评分 + {len(posts)}TapTap帖子 + {len(tieba_posts)}贴吧帖子 + {total_c}回复 + {tieba_replies}贴吧回复 + {total_n}嵌套 = {len(reviews)+len(posts)+len(tieba_posts)+total_c+total_n+tieba_replies}总互动")

    # 飞书推送（--no-push 时跳过，等待部署完成后用 --push-only 携带真实链接推送）
    if NO_PUSH:
        log("[跳过推送] 已指定 --no-push：请先部署 HTML，部署成功后运行 --push-only --report-url <链接> 完成推送")
        log("=" * 60)
        return

    if skip_if_pushed():
        return

    push_feishu_card(generate_chat_summary(all_items, DATE_LABEL, MODE_NAME, report_url=report_url))
    log("=" * 60)

if __name__ == "__main__":
    main()
