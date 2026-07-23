#!/usr/bin/env python3
"""
North Files - Instagram Ban/Recovery Monitor Bot
V6.1 - HikerAPI + Health Check Server for Render
100% Accurate | Crash-safe Database | Admin Only | Threaded
Commands: /remove /recover /postremove /postrecover /check /clear /watching /addadmin /removeadmin
"""

import os, re, io, asyncio, json, logging, threading, time, sqlite3, http.server, socketserver
from datetime import datetime, timezone, timedelta

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# ============================================================
# 🔥 APNA CONFIG YAHAN DAALO
# ============================================================
BOT_TOKEN = "8693740442:AAHAfZ0mr91h3W2r58b5uatte5f-QP0HJzg"
HIKERAPI_KEY = "u6l3fllahmnbpft4razg3s8nzuhm10e3"
ADMIN_IDS = [7691071175]
# ============================================================

# Render health check port
PORT = int(os.environ.get("PORT", 10000))

CHECK_INTERVAL = 90
PER_TARGET_DELAY = 3
REQUEST_TIMEOUT = 15
BRAND_NAME = "@Dochains • Monitoring"
DB_PATH = "north_files_monitor.db"

HIKERAPI_BASE = "https://api.hikerapi.com"
HIKERAPI_HEADERS = {"x-access-key": HIKERAPI_KEY}

POST_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
USER_RE = re.compile(r'^[A-Za-z0-9_.]{1,30}$')
IG_RE = re.compile(r'(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?', re.IGNORECASE)
ZERO_RE = re.compile(r'[\u200b-\u200f\u202a-\u202e\ufeff\xa0]')

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

main_app = None

# ============================================================
# DATABASE
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS monitored_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bot_token TEXT NOT NULL,
        username TEXT NOT NULL, display_name TEXT NOT NULL, mode TEXT NOT NULL,
        start_time TEXT NOT NULL, chat_id INTEGER NOT NULL, author_id INTEGER NOT NULL,
        last_stats TEXT, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bot_token, username))''')
    c.execute('''CREATE TABLE IF NOT EXISTS monitored_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bot_token TEXT NOT NULL,
        shortcode TEXT NOT NULL, url TEXT NOT NULL, mode TEXT NOT NULL,
        start_time TEXT NOT NULL, chat_id INTEGER NOT NULL, author_id INTEGER NOT NULL,
        last_stats TEXT, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bot_token, shortcode))''')
    c.execute('''CREATE TABLE IF NOT EXISTS bot_admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bot_token TEXT NOT NULL,
        user_id INTEGER NOT NULL, added_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bot_token, user_id))''')
    conn.commit()
    conn.close()
    logger.info(f"Database: {DB_PATH}")

# Account DB operations
def db_save_account(bot_token, username, display_name, mode, start_time, chat_id, author_id, last_stats):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('''INSERT OR REPLACE INTO monitored_accounts 
            (bot_token, username, display_name, mode, start_time, chat_id, author_id, last_stats, status)
            VALUES (?,?,?,?,?,?,?,?,'active')''',
            (bot_token, username.lower(), display_name, mode, start_time, chat_id, author_id, json.dumps(last_stats)))
        conn.commit()
    except Exception as e: logger.error(f"DB: {e}")
    finally: conn.close()

def db_remove_account(bot_token, username):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('UPDATE monitored_accounts SET status="completed" WHERE bot_token=? AND username=? AND status="active"',
                  (bot_token, username.lower()))
        conn.commit(); return c.rowcount > 0
    except: return False
    finally: conn.close()

def db_load_accounts(bot_token):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('SELECT username,display_name,mode,start_time,chat_id,author_id,last_stats FROM monitored_accounts WHERE bot_token=? AND status="active"', (bot_token,))
        accounts = []
        for row in c.fetchall():
            try: ls = json.loads(row[6]) if row[6] else {}
            except: ls = {"status": 200}
            accounts.append({"username":row[0],"display_name":row[1],"mode":row[2],
                "start":datetime.fromisoformat(row[3]) if row[3] else datetime.now(timezone.utc),
                "chat":row[4],"user":row[5],"last":ls})
        return accounts
    except: return []
    finally: conn.close()

# Post DB operations
def db_save_post(bot_token, shortcode, url, mode, start_time, chat_id, author_id, last_stats):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('''INSERT OR REPLACE INTO monitored_posts
            (bot_token, shortcode, url, mode, start_time, chat_id, author_id, last_stats, status)
            VALUES (?,?,?,?,?,?,?,?,'active')''',
            (bot_token, shortcode, url, mode, start_time, chat_id, author_id, json.dumps(last_stats)))
        conn.commit()
    except: pass
    finally: conn.close()

def db_remove_post(bot_token, shortcode):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('UPDATE monitored_posts SET status="completed" WHERE bot_token=? AND shortcode=? AND status="active"',
                  (bot_token, shortcode))
        conn.commit(); return c.rowcount > 0
    except: return False
    finally: conn.close()

def db_load_posts(bot_token):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('SELECT shortcode,url,mode,start_time,chat_id,author_id,last_stats FROM monitored_posts WHERE bot_token=? AND status="active"', (bot_token,))
        posts = []
        for row in c.fetchall():
            try: ls = json.loads(row[6]) if row[6] else {}
            except: ls = {"status": 200}
            posts.append({"shortcode":row[0],"url":row[1],"mode":row[2],
                "start":datetime.fromisoformat(row[3]) if row[3] else datetime.now(timezone.utc),
                "chat":row[4],"user":row[5],"last":ls})
        return posts
    except: return []
    finally: conn.close()

# Admin DB operations
def db_add_admin(bot_token, user_id, added_by):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('INSERT OR IGNORE INTO bot_admins (bot_token,user_id,added_by) VALUES (?,?,?)', (bot_token,user_id,added_by))
        conn.commit(); return c.rowcount > 0
    except: return False
    finally: conn.close()

def db_remove_admin(bot_token, user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('DELETE FROM bot_admins WHERE bot_token=? AND user_id=?', (bot_token,user_id))
        conn.commit(); return c.rowcount > 0
    except: return False
    finally: conn.close()

def db_get_admins(bot_token):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('SELECT user_id FROM bot_admins WHERE bot_token=?', (bot_token,))
        return [row[0] for row in c.fetchall()]
    except: return []
    finally: conn.close()

# ============================================================
# HIKERAPI - Instagram Data (100% Accurate)
# ============================================================

async def get_acc_stats(session, username):
    """HikerAPI se account info le"""
    url = f"{HIKERAPI_BASE}/v1/user/by/username"
    try:
        async with session.get(url, params={"username": username}, headers=HIKERAPI_HEADERS, timeout=REQUEST_TIMEOUT) as r:
            if r.status == 404:
                return {"status": 404}
            if r.status != 200:
                body = await r.text()
                logger.warning(f"HikerAPI status {r.status} for {username}: {body[:200]}")
                return {"status": r.status}
            data = await r.json()
    except Exception as e:
        logger.error(f"HikerAPI error for {username}: {e}")
        return {"status": 0}

    # Parse user data
    user = data.get("user", data)
    pic_url = None
    
    # Profile pic
    hd = user.get("hd_profile_pic_url_info") or {}
    pic_url = hd.get("url")
    if not pic_url:
        pic_url = user.get("profile_pic_url_hd")
    if not pic_url:
        pic_url = user.get("profile_pic_url")

    return {
        "status": 200,
        "pic": pic_url,
        "username": user.get("username", username),
        "full_name": user.get("full_name", ""),
        "followers": user.get("follower_count"),
        "following": user.get("following_count"),
        "posts": user.get("media_count"),
        "is_verified": user.get("is_verified", False),
        "is_private": user.get("is_private", False),
        "biography": user.get("biography", "")
    }

async def get_post_stats(session, url):
    """HikerAPI se post info le"""
    api_url = f"{HIKERAPI_BASE}/v2/media/info/by/url"
    try:
        async with session.get(api_url, params={"url": url}, headers=HIKERAPI_HEADERS, timeout=REQUEST_TIMEOUT) as r:
            if r.status == 404:
                return {"status": 404}
            if r.status != 200:
                return {"status": r.status}
            data = await r.json()
    except Exception as e:
        logger.error(f"HikerAPI post error: {e}")
        return {"status": 0}

    items = data.get("items", [])
    if not items:
        return {"status": 404}
    
    item = items[0]
    thumb_url = None
    try:
        candidates = (item.get("image_versions2") or {}).get("candidates") or []
        if candidates:
            thumb_url = candidates[0].get("url")
        if not thumb_url:
            thumb_url = item.get("thumbnail_url")
    except:
        pass

    return {
        "status": 200,
        "thumb": thumb_url,
        "like_count": item.get("like_count"),
        "comment_count": item.get("comment_count"),
        "code": item.get("code", "")
    }

async def dl_img(url):
    if not url: return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                if r.status == 200: return await r.read()
    except: pass
    return None

# ============================================================
# IMAGE GENERATION
# ============================================================

def get_fonts():
    try:
        return {"bl":ImageFont.truetype("DejaVuSans-Bold.ttf",30),"bm":ImageFont.truetype("DejaVuSans-Bold.ttf",24),
                "bs":ImageFont.truetype("DejaVuSans-Bold.ttf",20),"rm":ImageFont.truetype("DejaVuSans.ttf",20),
                "rs":ImageFont.truetype("DejaVuSans.ttf",17)}
    except:
        d=ImageFont.load_default(); return {k:d for k in ("bl","bm","bs","rm","rs")}

def circle_paste(base, pic_bytes, box, letter):
    x0,y0,x1,y1=box; sz=x1-x0
    mask=Image.new("L",(sz,sz),0)
    ImageDraw.Draw(mask).ellipse((0,0,sz,sz),fill=255)
    if pic_bytes:
        try:
            pic=Image.open(io.BytesIO(pic_bytes)).convert("RGB").resize((sz,sz))
            base.paste(pic,(x0,y0),mask); return
        except: pass
    c=Image.new("RGB",(sz,sz),(48,48,56))
    d=ImageDraw.Draw(c); f=ImageFont.load_default()
    try: f=ImageFont.truetype("DejaVuSans-Bold.ttf",sz//2)
    except: pass
    bb=d.textbbox((0,0),letter,font=f); tw,th=bb[2]-bb[0],bb[3]-bb[1]
    d.text(((sz-tw)/2,(sz-th)/2-bb[1]),letter,font=f,fill=(200,200,210))
    base.paste(c,(x0,y0),mask)
    return base

async def gen_screenshot(username, stats):
    W,H=680,220; fg=(255,255,255); muted=(168,168,176); blue=(0,149,246)
    panel=(18,18,21); border=(40,40,46)
    fonts=get_fonts(); pb=await dl_img(stats.get("pic"))
    base=Image.new("RGBA",(W,H),panel+(255,)); draw=ImageDraw.Draw(base)
    draw.rounded_rectangle([0,0,W-1,H-1],radius=18,outline=border,width=2)
    av=150; ax,ay=25,(H-av)//2
    circle_paste(base,pb,(ax,ay,ax+av,ay+av),username[:1].upper())
    tx=ax+av+28; ty=34
    draw.text((tx,ty),username,font=fonts["bl"],fill=fg)
    nb=draw.textbbox((tx,ty),username,font=fonts["bl"]); x=nb[2]+8
    if stats.get("is_verified"):
        vs=22; vy=ty+6
        draw.ellipse([x,vy,x+vs,vy+vs],fill=blue)
        draw.line([x+5,vy+11,x+9,vy+15,x+17,vy+5],fill=(255,255,255),width=3); x+=vs+8
    draw.text((x,ty+3),"›",font=fonts["rm"],fill=muted)
    bt="Follow"; bf=fonts["bs"]
    bb=draw.textbbox((0,0),bt,font=bf); bw=(bb[2]-bb[0])+34; bh=38
    bx0=W-25-bw; by0=ty-2
    draw.rounded_rectangle([bx0,by0,bx0+bw,by0+bh],radius=8,fill=blue)
    tb=draw.textbbox((0,0),bt,font=bf); tw,th=tb[2]-tb[0],tb[3]-tb[1]
    draw.text((bx0+(bw-tw)/2,by0+(bh-th)/2-tb[1]),bt,font=bf,fill=(255,255,255))
    ry=ty+42; fn=(stats.get("full_name") or "").strip()
    if fn and fn.lower()!=username.lower():
        draw.text((tx,ry),fn,font=fonts["rm"],fill=muted); ry+=30
    def fmt(n):
        if n is None: n=0
        if n>=1000000: return f"{n/1000000:.1f}".rstrip("0").rstrip(".")+"M"
        if n>=10000: return f"{n/1000:.1f}".rstrip("0").rstrip(".")+"K"
        return f"{n:,}"
    fs=fmt(stats.get("followers")); fgs=fmt(stats.get("following")); sx=tx
    draw.text((sx,ry),fs,font=fonts["bs"],fill=fg)
    sb=draw.textbbox((sx,ry),fs,font=fonts["bs"]); sx=sb[2]+6
    draw.text((sx,ry+2),"followers",font=fonts["rs"],fill=muted)
    sb2=draw.textbbox((sx,ry+2),"followers",font=fonts["rs"]); sx=sb2[2]+20
    draw.text((sx,ry),fgs,font=fonts["bs"],fill=fg)
    sb3=draw.textbbox((sx,ry),fgs,font=fonts["bs"]); sx=sb3[2]+6
    draw.text((sx,ry+2),"following",font=fonts["rs"],fill=muted)
    cm=Image.new("L",(W,H),0)
    ImageDraw.Draw(cm).rounded_rectangle([0,0,W-1,H-1],radius=18,fill=255)
    base.putalpha(cm); buf=io.BytesIO(); base.save(buf,format="PNG"); buf.seek(0)
    return buf

# ============================================================
# HEALTH CHECK SERVER (Render ke liye)
# ============================================================

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "healthy",
            "bot": "running",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }).encode())
    
    def log_message(self, format, *args):
        logger.debug(f"Health: {args}")

def run_health_server():
    """Background health check server for Render"""
    try:
        with socketserver.TCPServer(("", PORT), HealthHandler) as httpd:
            logger.info(f"✅ Health server on port {PORT}")
            httpd.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {e}")

# ============================================================
# HELPERS
# ============================================================

def clean_user(raw):
    raw=raw.strip(); raw=ZERO_RE.sub("",raw)
    m=IG_RE.search(raw)
    if m: raw=m.group(1)
    return raw.lstrip("@").strip()

def get_shortcode(link):
    m=POST_RE.search(link); return m.group(1) if m else None

def now_str(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def fmt_dur(start, end):
    t=int((end-start).total_seconds()); h,r=divmod(t,3600); m,s=divmod(r,60)
    def p(n,w): return f"{n} {w}" if n==1 else f"{n} {w}s"
    return f"{p(h,'hour')}, {p(m,'minute')}, {p(s,'second')}"

def is_admin(user_id):
    if user_id in ADMIN_IDS: return True
    return user_id in db_get_admins(BOT_TOKEN)

async def acc_caption(event, username, stats, start):
    now=datetime.now(timezone.utc); dur=fmt_dur(start,now)
    f=stats.get("followers"); fs="N/A" if f is None else f"{f:,}"
    if event=="recovered": em="🏆✅"; t="Account Recovered"; a="Unbanned"
    else: em="🪦❌"; t="Account Removed"; a="Banned"
    return f"<b>{t}</b> {em}\n<b>Username:</b> @{username}\n<b>Followers:</b> {fs}\n<b>Duration:</b> {dur}\n<i>{a} at {now_str()} UTC</i>\n<code>{BRAND_NAME}</code>"

def post_caption(event, sc, url, stats, start):
    now=datetime.now(timezone.utc); dur=fmt_dur(start,now)
    if event=="recovered": em="✅"; t="Post Recovered"; a="Restored"
    else: em="🪦❌"; t="Post Removed"; a="Removed"
    return f"<b>{t}</b> {em}\n<b>Shortcode:</b> <code>{sc}</code>\n<b>Link:</b> {url}\n<b>Duration:</b> {dur}\n<i>{a} at {now_str()} UTC</i>\n<code>{BRAND_NAME}</code>"

# ============================================================
# COMMANDS
# ============================================================

async def start_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized."); return
    await update.message.reply_text(
        f"<b>North Files - Instagram Monitor Bot</b>\n\n"
        f"<b>Commands:</b>\n"
        f"<code>/remove username</code> - Watch active, alert when banned\n"
        f"<code>/recover username</code> - Watch banned, alert when recovered\n"
        f"<code>/postremove link</code> - Watch post, alert when removed\n"
        f"<code>/postrecover link</code> - Watch removed post, alert when restored\n"
        f"<code>/check username</code> - Check if active or banned (with photo)\n"
        f"<code>/clear username</code> - Remove from monitoring\n"
        f"<code>/watching</code> - List tracked items\n"
        f"<code>/addadmin user_id</code> - Add admin\n"
        f"<code>/removeadmin user_id</code> - Remove admin\n\n"
        f"<i>Powered by HikerAPI | Crash-safe Database</i>\n"
        f"<code>{BRAND_NAME}</code>", parse_mode=ParseMode.HTML)

async def remove_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /remove username"); return
    u=clean_user(context.args[0])
    if not USER_RE.match(u): await update.message.reply_text("Invalid username."); return
    msg=await update.message.reply_text("⏳ Checking...")
    async with aiohttp.ClientSession(headers=HIKERAPI_HEADERS) as s: stats=await get_acc_stats(s, u)
    if stats.get("status")==404: await msg.edit_text(f"❌ @{u} already 404."); return
    if stats.get("status")!=200: await msg.edit_text(f"⚠️ Cannot reach @{u}."); return
    now=datetime.now(timezone.utc).isoformat()
    db_save_account(BOT_TOKEN,u,u,"remove",now,update.effective_chat.id,update.effective_user.id,stats)
    await msg.edit_text(f"🔍 Watching @{u} - alert when BANNED.\n✅ Saved to DB!", parse_mode=ParseMode.HTML)

async def recover_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /recover username"); return
    u=clean_user(context.args[0])
    if not USER_RE.match(u): await update.message.reply_text("Invalid username."); return
    msg=await update.message.reply_text("⏳ Checking...")
    async with aiohttp.ClientSession(headers=HIKERAPI_HEADERS) as s: stats=await get_acc_stats(s, u)
    if stats.get("status")==200: await msg.edit_text(f"✅ @{u} already active."); return
    now=datetime.now(timezone.utc).isoformat()
    db_save_account(BOT_TOKEN,u,u,"recover",now,update.effective_chat.id,update.effective_user.id,{"status":stats.get("status",404)})
    await msg.edit_text(f"🔍 Watching @{u} - alert when RECOVERED.\n✅ Saved to DB!", parse_mode=ParseMode.HTML)

async def postremove_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /postremove link"); return
    link=context.args[0]; sc=get_shortcode(link)
    if not sc: await update.message.reply_text("Invalid link."); return
    msg=await update.message.reply_text("⏳ Checking post...")
    async with aiohttp.ClientSession(headers=HIKERAPI_HEADERS) as s: stats=await get_post_stats(s, link)
    if stats.get("status")==404: await msg.edit_text("❌ Post already removed."); return
    if stats.get("status")!=200: await msg.edit_text("⚠️ Cannot reach post."); return
    now=datetime.now(timezone.utc).isoformat()
    db_save_post(BOT_TOKEN,sc,link,"remove",now,update.effective_chat.id,update.effective_user.id,stats)
    await msg.edit_text(f"🔍 Watching post <code>{sc}</code> for removal.\n✅ Saved to DB!", parse_mode=ParseMode.HTML)

async def postrecover_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /postrecover link"); return
    link=context.args[0]; sc=get_shortcode(link)
    if not sc: await update.message.reply_text("Invalid link."); return
    msg=await update.message.reply_text("⏳ Checking post...")
    async with aiohttp.ClientSession(headers=HIKERAPI_HEADERS) as s: stats=await get_post_stats(s, link)
    if stats.get("status")==200: await msg.edit_text("✅ Post already live."); return
    now=datetime.now(timezone.utc).isoformat()
    db_save_post(BOT_TOKEN,sc,link,"recover",now,update.effective_chat.id,update.effective_user.id,{"status":stats.get("status",404)})
    await msg.edit_text(f"🔍 Watching post <code>{sc}</code> for recovery.\n✅ Saved to DB!", parse_mode=ParseMode.HTML)

async def check_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /check username"); return
    u=clean_user(context.args[0])
    if not USER_RE.match(u): await update.message.reply_text("Invalid username."); return
    msg=await update.message.reply_text(f"🔍 Checking @{u}...")
    async with aiohttp.ClientSession(headers=HIKERAPI_HEADERS) as s: stats=await get_acc_stats(s, u)
    
    if stats.get("status")==404:
        await msg.edit_text(f"❌ <b>@{u}</b> is <b>NOT ACTIVE</b>\n\nThe account is banned or removed.\nStatus: 404\n<code>{BRAND_NAME}</code>", parse_mode=ParseMode.HTML)
    elif stats.get("status")==200:
        try:
            fb=await gen_screenshot(u, stats)
            cap=(f"✅ <b>@{u}</b> is <b>ACTIVE</b>\n\n"
                 f"<b>Followers:</b> {stats.get('followers','N/A') or 'N/A'}\n"
                 f"<b>Following:</b> {stats.get('following','N/A') or 'N/A'}\n"
                 f"<b>Posts:</b> {stats.get('posts','N/A') or 'N/A'}\n"
                 f"<b>Verified:</b> {'Yes ✅' if stats.get('is_verified') else 'No'}\n"
                 f"<code>{BRAND_NAME}</code>")
            await msg.delete()
            await update.message.reply_photo(photo=fb, caption=cap, parse_mode=ParseMode.HTML)
        except:
            await msg.edit_text(f"✅ <b>@{u}</b> is <b>ACTIVE</b>\n\nFollowers: {stats.get('followers','N/A')}\n<code>{BRAND_NAME}</code>", parse_mode=ParseMode.HTML)
    else:
        await msg.edit_text(f"⚠️ Could not check @{u}. Try again.")

async def clear_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /clear username"); return
    u=clean_user(context.args[0])
    if not USER_RE.match(u): await update.message.reply_text("Invalid username."); return
    removed=db_remove_account(BOT_TOKEN,u)
    sc=get_shortcode(context.args[0]); removed_post=False
    if sc: removed_post=db_remove_post(BOT_TOKEN,sc)
    if removed: await update.message.reply_text(f"✅ @{u} removed from monitoring.", parse_mode=ParseMode.HTML)
    elif removed_post: await update.message.reply_text(f"✅ Post {sc} removed.", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(f"⚠️ @{u} was not in monitoring.", parse_mode=ParseMode.HTML)

async def watching_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    accounts=db_load_accounts(BOT_TOKEN); posts=db_load_posts(BOT_TOKEN)
    al=[f"• @{a['username']} ({a['mode']}) - {fmt_dur(a['start'],datetime.now(timezone.utc))}" for a in accounts]
    pl=[f"• <code>{p['shortcode']}</code> ({p['mode']}) - {fmt_dur(p['start'],datetime.now(timezone.utc))}" for p in posts]
    if not al and not pl: await update.message.reply_text("Nothing monitored."); return
    m=["<b>👁️ Currently Watching</b>\n"]
    if al: m.append("<b>Accounts:</b>"); m.extend(al); m.append("")
    if pl: m.append("<b>Posts:</b>"); m.extend(pl); m.append("")
    m.append(f"<code>{BRAND_NAME}</code>")
    await update.message.reply_text("\n".join(m), parse_mode=ParseMode.HTML)

async def addadmin_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /addadmin user_id"); return
    try: new_id=int(context.args[0].strip())
    except: await update.message.reply_text("❌ Invalid ID."); return
    if is_admin(new_id): await update.message.reply_text(f"⚠️ Already admin.", parse_mode=ParseMode.HTML); return
    if db_add_admin(BOT_TOKEN,new_id,update.effective_user.id):
        await update.message.reply_text(f"✅ Admin <code>{new_id}</code> added!", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text("❌ Failed.")

async def removeadmin_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /removeadmin user_id"); return
    try: rem_id=int(context.args[0].strip())
    except: await update.message.reply_text("❌ Invalid ID."); return
    if rem_id in ADMIN_IDS: await update.message.reply_text(f"❌ Cannot remove hardcoded admin.", parse_mode=ParseMode.HTML); return
    if db_remove_admin(BOT_TOKEN,rem_id):
        await update.message.reply_text(f"✅ Admin <code>{rem_id}</code> removed!", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(f"❌ Not found.")

# ============================================================
# MONITOR LOOP
# ============================================================

def run_monitor():
    global main_app
    logger.info("Monitor thread started")
    time.sleep(15)  # Wait for bot to initialize
    
    while True:
        try:
            accounts = db_load_accounts(BOT_TOKEN)
            posts = db_load_posts(BOT_TOKEN)
            
            if accounts or posts:
                logger.info(f"Sweep: {len(accounts)} accounts, {len(posts)} posts")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(do_sweep(accounts, posts))
                loop.close()
            
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            time.sleep(30)

async def do_sweep(accounts, posts):
    global main_app
    if not main_app: return
    
    try:
        async with aiohttp.ClientSession(headers=HIKERAPI_HEADERS) as session:
            for acc in accounts:
                try:
                    stats = await get_acc_stats(session, acc["username"])
                except:
                    await asyncio.sleep(PER_TARGET_DELAY)
                    continue
                
                fired = None
                if stats.get("status") == 404 and acc["mode"] == "remove": fired = "removed"
                elif stats.get("status") == 200 and acc["mode"] == "recover": fired = "recovered"
                
                if fired:
                    us = stats if fired == "recovered" else acc["last"]
                    cap = await acc_caption(fired, acc["username"], us, acc["start"])
                    try:
                        fb = await gen_screenshot(acc["username"], us)
                        await main_app.bot.send_photo(chat_id=acc["chat"], photo=fb, caption=cap, parse_mode=ParseMode.HTML)
                        logger.info(f"✅ Alert: @{acc['username']} {fired}")
                    except:
                        try: await main_app.bot.send_message(chat_id=acc["chat"], text=cap, parse_mode=ParseMode.HTML)
                        except: pass
                    db_remove_account(BOT_TOKEN, acc["username"])
                
                await asyncio.sleep(PER_TARGET_DELAY)
            
            for p in posts:
                try:
                    stats = await get_post_stats(session, p["url"])
                except:
                    await asyncio.sleep(PER_TARGET_DELAY)
                    continue
                
                fired = None
                if stats.get("status") == 404 and p["mode"] == "remove": fired = "removed"
                elif stats.get("status") == 200 and p["mode"] == "recover": fired = "recovered"
                
                if fired:
                    cap = post_caption(fired, p["shortcode"], p["url"], 
                                       stats if fired=="recovered" else p["last"], p["start"])
                    try: await main_app.bot.send_message(chat_id=p["chat"], text=cap, parse_mode=ParseMode.HTML)
                    except: pass
                    db_remove_post(BOT_TOKEN, p["shortcode"])
                
                await asyncio.sleep(PER_TARGET_DELAY)
    except Exception as e:
        logger.error(f"Sweep: {e}")

# ============================================================
# MAIN
# ============================================================

def main():
    global main_app
    
    print("""
╔══════════════════════════════════════════╗
║  North Files - Instagram Monitor Bot    ║
║  V6.1 - HikerAPI + Health Check Server ║
║  100% Accurate | Crash-safe | DB       ║
╚══════════════════════════════════════════╝
    """)
    
    init_db()
    
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ BOT_TOKEN set karo!"); return
    if not HIKERAPI_KEY or HIKERAPI_KEY == "YOUR_HIKERAPI_KEY_HERE":
        print("❌ HIKERAPI_KEY set karo! https://hikerapi.com"); return
    
    print(f"✅ Bot: {BOT_TOKEN[:15]}...")
    print(f"✅ HikerAPI: Configured")
    print(f"✅ Admins: {ADMIN_IDS}")
    print(f"✅ Health Check Port: {PORT}")
    
    db_admins = db_get_admins(BOT_TOKEN)
    if db_admins: print(f"✅ DB Admins: {db_admins}")
    
    # Start health check server (Render ke liye)
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    print(f"✅ Health server running on port {PORT}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    main_app = app
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("recover", recover_cmd))
    app.add_handler(CommandHandler("postremove", postremove_cmd))
    app.add_handler(CommandHandler("postrecover", postrecover_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("watching", watching_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("removeadmin", removeadmin_cmd))
    
    t = threading.Thread(target=run_monitor, daemon=True)
    t.start()
    
    print("\n✅ Bot Ready! HikerAPI - 100% Accurate!")
    print(f"✅ Health check: http://localhost:{PORT}/")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()