#!/usr/bin/env python3
"""
North Files - Instagram Ban/Recovery Monitor Bot
V2.0 - Multi-Bot Support + Database Persistence + Photo Generation
SQLite database stores all monitoring data
Crash-safe: restarts resume exactly where left off.
Commands: /remove /recover /postremove /postrecover /watching /check
"""

import os
import re
import io
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
import aiohttp
from PIL import Image, ImageDraw, ImageFont

# ============================================================
# 🔥 CONFIGURATION
# ============================================================
BOT_TOKEN = "8693740442:AAHAfZ0mr91h3W2r58b5uatte5f-QP0HJzg"
HIKERAPI_KEY = "l85q1f6ohwhm51f0hmpnvwsdp9d00mdv"
ADMIN_IDS = [7691071175]

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

# Global registry to manage running bot instances dynamically
RUNNING_BOTS = {}

# ============================================================
# DATABASE OPERATIONS
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS monitored_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_token TEXT NOT NULL,
            username TEXT NOT NULL,
            display_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            start_time TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            last_stats TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_token, username)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS monitored_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_token TEXT NOT NULL,
            shortcode TEXT NOT NULL,
            url TEXT NOT NULL,
            mode TEXT NOT NULL,
            start_time TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            last_stats TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_token, shortcode)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_token TEXT UNIQUE NOT NULL,
            admin_ids TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def db_save_account(bot_token, username, display_name, mode, start_time, chat_id, author_id, last_stats):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''
            INSERT OR REPLACE INTO monitored_accounts 
            (bot_token, username, display_name, mode, start_time, chat_id, author_id, last_stats, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
        ''', (bot_token, username.lower(), display_name, mode, start_time, chat_id, author_id, json.dumps(last_stats)))
        conn.commit()
    except Exception as e:
        logger.error(f"DB save account error: {e}")
    finally:
        conn.close()

def db_remove_account(bot_token, username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''
            UPDATE monitored_accounts SET status = 'completed' 
            WHERE bot_token = ? AND username = ? AND status = 'active'
        ''', (bot_token, username.lower()))
        conn.commit()
    except Exception as e:
        logger.error(f"DB remove account error: {e}")
    finally:
        conn.close()

def db_load_accounts(bot_token=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        if bot_token:
            c.execute('''
                SELECT username, display_name, mode, start_time, chat_id, author_id, last_stats, bot_token
                FROM monitored_accounts WHERE bot_token = ? AND status = 'active'
            ''', (bot_token,))
        else:
            c.execute('''
                SELECT username, display_name, mode, start_time, chat_id, author_id, last_stats, bot_token
                FROM monitored_accounts WHERE status = 'active'
            ''')
        rows = c.fetchall()
        accounts = []
        for row in rows:
            try:
                last_stats = json.loads(row[6]) if row[6] else {}
            except:
                last_stats = {"status": 404}
            accounts.append({
                "username": row[0],
                "display_name": row[1],
                "mode": row[2],
                "start": datetime.fromisoformat(row[3]) if row[3] else datetime.now(timezone.utc),
                "chat": row[4],
                "user": row[5],
                "last": last_stats,
                "bot_token": row[7]
            })
        return accounts
    except Exception as e:
        logger.error(f"DB load accounts error: {e}")
        return []
    finally:
        conn.close()

def db_save_post(bot_token, shortcode, url, mode, start_time, chat_id, author_id, last_stats):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''
            INSERT OR REPLACE INTO monitored_posts
            (bot_token, shortcode, url, mode, start_time, chat_id, author_id, last_stats, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
        ''', (bot_token, shortcode, url, mode, start_time, chat_id, author_id, json.dumps(last_stats)))
        conn.commit()
    except Exception as e:
        logger.error(f"DB save post error: {e}")
    finally:
        conn.close()

def db_remove_post(bot_token, shortcode):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''
            UPDATE monitored_posts SET status = 'completed'
            WHERE bot_token = ? AND shortcode = ? AND status = 'active'
        ''', (bot_token, shortcode))
        conn.commit()
    except Exception as e:
        logger.error(f"DB remove post error: {e}")
    finally:
        conn.close()

def db_load_posts(bot_token=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        if bot_token:
            c.execute('''
                SELECT shortcode, url, mode, start_time, chat_id, author_id, last_stats, bot_token
                FROM monitored_posts WHERE bot_token = ? AND status = 'active'
            ''', (bot_token,))
        else:
            c.execute('''
                SELECT shortcode, url, mode, start_time, chat_id, author_id, last_stats, bot_token
                FROM monitored_posts WHERE status = 'active'
            ''')
        rows = c.fetchall()
        posts = []
        for row in rows:
            try:
                last_stats = json.loads(row[6]) if row[6] else {}
            except:
                last_stats = {"status": 404}
            posts.append({
                "shortcode": row[0],
                "url": row[1],
                "mode": row[2],
                "start": datetime.fromisoformat(row[3]) if row[3] else datetime.now(timezone.utc),
                "chat": row[4],
                "user": row[5],
                "last": last_stats,
                "bot_token": row[7]
            })
        return posts
    except Exception as e:
        logger.error(f"DB load posts error: {e}")
        return []
    finally:
        conn.close()

def save_bot_config(bot_token, admin_ids):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''
            INSERT OR REPLACE INTO bot_configs (bot_token, admin_ids, is_active)
            VALUES (?, ?, 1)
        ''', (bot_token, json.dumps(admin_ids)))
        conn.commit()
    except Exception as e:
        logger.error(f"DB save bot config error: {e}")
    finally:
        conn.close()

def load_all_bot_configs():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('SELECT bot_token, admin_ids FROM bot_configs WHERE is_active = 1')
        rows = c.fetchall()
        configs = []
        for row in rows:
            try:
                admins = json.loads(row[1])
                configs.append({"token": row[0], "admins": admins})
            except:
                configs.append({"token": row[0], "admins": []})
        return configs
    except Exception as e:
        logger.error(f"DB load bot configs error: {e}")
        return []
    finally:
        conn.close()

# ============================================================
# UTILITIES & IG ENGINE
# ============================================================

def clean_user(raw):
    raw = raw.strip().split('?')[0]
    raw = ZERO_RE.sub("", raw)
    m = IG_RE.search(raw)
    if m: raw = m.group(1)
    return raw.lstrip("@").strip()

def get_shortcode(link):
    m = POST_RE.search(link)
    return m.group(1) if m else None

def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def fmt_dur(start, end):
    t = int((end - start).total_seconds())
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    def p(n, w): return f"{n} {w}" if n == 1 else f"{n} {w}s"
    return f"{p(h, 'hour')}, {p(m, 'minute')}, {p(s, 'second')}"

def fmt_num(n):
    if n is None: return "0"
    if n >= 1000000: return f"{n/1000000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000: return f"{n/1000:.1f}".rstrip("0").rstrip(".") + "K"
    return f"{n:,}"

async def get_acc_stats(session, username):
    url = f"{HIKERAPI_BASE}/v1/user/by/username"
    try:
        async with session.get(url, params={"username": username}, headers=HIKERAPI_HEADERS, timeout=REQUEST_TIMEOUT) as r:
            if r.status == 404: return {"status": 404}
            if r.status != 200: return {"status": r.status}
            data = await r.json()
    except Exception as e:
        logger.error(f"HikerAPI fetch error for {username}: {e}")
        return {"status": "error"}
    
    return {
        "status": 200,
        "followers": data.get("follower_count"),
        "following": data.get("following_count"),
        "posts": data.get("media_count"),
        "pic": data.get("profile_pic_url"),
        "full_name": data.get("full_name") or username,
        "biography": data.get("biography") or "",
        "is_verified": bool(data.get("is_verified")),
    }

async def get_post_stats(session, post_url):
    url = f"{HIKERAPI_BASE}/v2/media/info/by/url"
    try:
        async with session.get(url, params={"url": post_url}, headers=HIKERAPI_HEADERS, timeout=REQUEST_TIMEOUT) as r:
            if r.status == 404: return {"status": 404}
            if r.status != 200: return {"status": r.status}
            data = await r.json()
    except Exception as e:
        logger.error(f"HikerAPI post fetch error: {e}")
        return {"status": "error"}
    
    thumb_url = None
    try:
        candidates = (data.get("image_versions2") or {}).get("candidates") or []
        if candidates: thumb_url = candidates[0].get("url")
        if not thumb_url: thumb_url = data.get("thumbnail_url")
    except: pass
    
    return {"status": 200, "thumb": thumb_url}

async def dl_img(url):
    if not url: return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                if r.status == 200: return await r.read()
    except: pass
    return None

def get_fonts():
    try:
        return {
            "bl": ImageFont.truetype("DejaVuSans-Bold.ttf", 30),
            "bm": ImageFont.truetype("DejaVuSans-Bold.ttf", 24),
            "bs": ImageFont.truetype("DejaVuSans-Bold.ttf", 20),
            "rm": ImageFont.truetype("DejaVuSans.ttf", 20),
            "rs": ImageFont.truetype("DejaVuSans.ttf", 17),
        }
    except Exception:
        # Fallback if TTF files aren't in Render system path
        d = ImageFont.load_default()
        return {"bl": d, "bm": d, "bs": d, "rm": d, "rs": d}

def circle_paste(base, pic_bytes, box, letter):
    x0, y0, x1, y1 = box; sz = x1 - x0
    mask = Image.new("L", (sz, sz), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, sz, sz), fill=255)
    if pic_bytes:
        try:
            pic = Image.open(io.BytesIO(pic_bytes)).convert("RGB").resize((sz, sz))
            base.paste(pic, (x0, y0), mask)
            return
        except Exception: pass
    c = Image.new("RGB", (sz, sz), (48, 48, 56))
    d = ImageDraw.Draw(c)
    f = ImageFont.load_default()
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", sz // 2)
    except Exception: pass
    bb = d.textbbox((0, 0), letter, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((sz - tw) / 2, (sz - th) / 2 - bb[1]), letter, font=f, fill=(200, 200, 210))
    base.paste(c, (x0, y0), mask)

async def gen_screenshot(username, stats):
    W, H = 680, 220
    fg = (255, 255, 255); muted = (168, 168, 176); blue = (0, 149, 246)
    panel = (18, 18, 21); border = (40, 40, 46)
    fonts = get_fonts()
    pb = await dl_img(stats.get("pic"))
    
    base = Image.new("RGBA", (W, H), panel + (255,))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=18, outline=border, width=2)
    
    av = 150; ax, ay = 25, (H - av) // 2
    circle_paste(base, pb, (ax, ay, ax + av, ay + av), username[:1].upper())
    
    tx = ax + av + 28; ty = 34
    draw.text((tx, ty), username, font=fonts["bl"], fill=fg)
    nb = draw.textbbox((tx, ty), username, font=fonts["bl"]); x = nb[2] + 8
    
    if stats.get("is_verified"):
        vs = 22; vy = ty + 6
        draw.ellipse([x, vy, x + vs, vy + vs], fill=blue)
        draw.line([x + 5, vy + 11, x + 9, vy + 15, x + 17, vy + 5], fill=(255, 255, 255), width=3)
        x += vs + 8
        
    draw.text((x, ty + 3), "›", font=fonts["rm"], fill=muted)
    
    bt = "Follow"; bf = fonts["bs"]
    bb = draw.textbbox((0, 0), bt, font=bf); bw = (bb[2] - bb[0]) + 34; bh = 38
    bx0 = W - 25 - bw; by0 = ty - 2
    draw.rounded_rectangle([bx0, by0, bx0 + bw, by0 + bh], radius=8, fill=blue)
    tb = draw.textbbox((0, 0), bt, font=bf); tw, th = tb[2] - tb[0], tb[3] - tb[1]
    draw.text((bx0 + (bw - tw) / 2, by0 + (bh - th) / 2 - tb[1]), bt, font=bf, fill=(255, 255, 255))
    
    ry = ty + 42
    fn = (stats.get("full_name") or "").strip()
    if fn and fn.lower() != username.lower():
        draw.text((tx, ry), fn, font=fonts["rm"], fill=muted); ry += 30
        
    fs = fmt_num(stats.get("followers"))
    fgs = fmt_num(stats.get("following"))
    sx = tx
    
    draw.text((sx, ry), fs, font=fonts["bs"], fill=fg)
    sb = draw.textbbox((sx, ry), fs, font=fonts["bs"]); sx = sb[2] + 6
    draw.text((sx, ry + 2), "followers", font=fonts["rs"], fill=muted)
    sb2 = draw.textbbox((sx, ry + 2), "followers", font=fonts["rs"]); sx = sb2[2] + 20
    
    draw.text((sx, ry), fgs, font=fonts["bs"], fill=fg)
    sb3 = draw.textbbox((sx, ry), fgs, font=fonts["bs"]); sx = sb3[2] + 6
    draw.text((sx, ry + 2), "following", font=fonts["rs"], fill=muted)
    
    cm = Image.new("L", (W, H), 0)
    ImageDraw.Draw(cm).rounded_rectangle([0, 0, W - 1, H - 1], radius=18, fill=255)
    base.putalpha(cm)
    
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def acc_caption(event, username, stats, start):
    now = datetime.now(timezone.utc)
    dur = fmt_dur(start, now)
    f = stats.get("followers")
    fs = "N/A" if f is None else fmt_num(f)
    
    if event == "recovered":
        em, t, a = "🏆✅", "Account Recovered", "Unbanned"
    else:
        em, t, a = "🪦❌", "Account Removed", "Banned"
        
    return (
        f"<b>{t}</b> {em}\n"
        f"<b>Username:</b> @{username}\n"
        f"<b>Followers:</b> {fs}\n"
        f"<b>Duration:</b> {dur}\n"
        f"<i>{a} at {now_str()} UTC</i>\n"
        f"<code>{BRAND_NAME}</code>"
    )

def post_caption(event, sc, url, stats, start):
    now = datetime.now(timezone.utc)
    dur = fmt_dur(start, now)
    if event == "recovered":
        em, t, a = "✅", "Post Recovered", "Restored"
    else:
        em, t, a = "🪦❌", "Post Removed", "Removed"
    return f"<b>{t}</b> {em}\n<b>Shortcode:</b> <code>{sc}</code>\n<b>Link:</b> {url}\n<b>Duration:</b> {dur}\n<i>{a} at {now_str()} UTC</i>\n<code>{BRAND_NAME}</code>"

# ============================================================
# TELEGRAM BOT HANDLERS
# ============================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"<b>North Files - Instagram Monitor Bot</b>\n\n"
        f"<b>Commands:</b>\n"
        f"<code>/remove username</code> - Watch active, alert when banned\n"
        f"<code>/recover username</code> - Watch banned, alert when recovered\n"
        f"<code>/postremove link</code> - Watch post, alert when removed\n"
        f"<code>/postrecover link</code> - Watch removed post, alert when restored\n"
        f"<code>/check username</code> - Check if account is active or banned\n"
        f"<code>/watching</code> - List tracked items\n\n"
        f"<i>Database: Crash-safe monitoring</i>\n"
        f"<code>{BRAND_NAME}</code>",
        parse_mode=ParseMode.HTML)

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /remove username"); return
    u = clean_user(context.args[0])
    if not USER_RE.match(u): await update.message.reply_text("Invalid username."); return
    
    msg = await update.message.reply_text("⏳ Checking...")
    async with aiohttp.ClientSession() as s:
        stats = await get_acc_stats(s, u)
    
    if stats.get("status") == 404:
        await msg.edit_text(f"❌ @{u} already 404."); return
    if stats.get("status") != 200:
        await msg.edit_text("Cannot reach account."); return
    
    token = context.bot.token
    now = datetime.now(timezone.utc).isoformat()
    db_save_account(token, u, u, "remove", now, update.effective_chat.id, update.effective_user.id, stats)
    
    await msg.edit_text(f"🔍 Watching @{u} - alert when BANNED.\n✅ Saved to database!", parse_mode=ParseMode.HTML)

async def recover_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /recover username"); return
    u = clean_user(context.args[0])
    if not USER_RE.match(u): await update.message.reply_text("Invalid username."); return
    
    msg = await update.message.reply_text("⏳ Checking...")
    async with aiohttp.ClientSession() as s:
        stats = await get_acc_stats(s, u)
    
    if stats.get("status") == 200:
        await msg.edit_text(f"✅ @{u} already active."); return
    
    token = context.bot.token
    now = datetime.now(timezone.utc).isoformat()
    db_save_account(token, u, u, "recover", now, update.effective_chat.id, update.effective_user.id, {"status": stats.get("status", 404)})
    
    await msg.edit_text(f"🔍 Watching @{u} - alert when RECOVERED.\n✅ Saved to database!", parse_mode=ParseMode.HTML)

async def postremove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /postremove link"); return
    link = context.args[0]; sc = get_shortcode(link)
    if not sc: await update.message.reply_text("Invalid link."); return
    
    msg = await update.message.reply_text("⏳ Checking post...")
    async with aiohttp.ClientSession() as s:
        stats = await get_post_stats(s, link)
    
    if stats.get("status") == 404: await msg.edit_text("❌ Post already removed."); return
    if stats.get("status") != 200: await msg.edit_text("Cannot reach post."); return
    
    token = context.bot.token
    now = datetime.now(timezone.utc).isoformat()
    db_save_post(token, sc, link, "remove", now, update.effective_chat.id, update.effective_user.id, stats)
    
    await msg.edit_text(f"🔍 Watching post <code>{sc}</code> for removal.\n✅ Saved to database!", parse_mode=ParseMode.HTML)

async def postrecover_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /postrecover link"); return
    link = context.args[0]; sc = get_shortcode(link)
    if not sc: await update.message.reply_text("Invalid link."); return
    
    msg = await update.message.reply_text("⏳ Checking post...")
    async with aiohttp.ClientSession() as s:
        stats = await get_post_stats(s, link)
    
    if stats.get("status") == 200: await msg.edit_text("✅ Post already live."); return
    
    token = context.bot.token
    now = datetime.now(timezone.utc).isoformat()
    db_save_post(token, sc, link, "recover", now, update.effective_chat.id, update.effective_user.id, {"status": stats.get("status", 404)})
    
    await msg.edit_text(f"🔍 Watching post <code>{sc}</code> for recovery.\n✅ Saved to database!", parse_mode=ParseMode.HTML)

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /check username"); return
    u = clean_user(context.args[0])
    if not USER_RE.match(u): await update.message.reply_text("Invalid username."); return
    
    msg = await update.message.reply_text(f"🔍 Checking @{u}...")
    async with aiohttp.ClientSession() as s:
        stats = await get_acc_stats(s, u)
    
    if stats.get("status") == 404:
        await msg.edit_text(f"❌ <b>@{u}</b> is <b>NOT ACTIVE</b>\n\nStatus: 404 Not Found\n<code>{BRAND_NAME}</code>", parse_mode=ParseMode.HTML)
    elif stats.get("status") == 200:
        try:
            fb = await gen_screenshot(u, stats)
            cap = (
                f"✅ <b>@{u}</b> is <b>ACTIVE</b>\n\n"
                f"<b>Followers:</b> {fmt_num(stats.get('followers', 0))}\n"
                f"<b>Following:</b> {fmt_num(stats.get('following', 0))}\n"
                f"<b>Posts:</b> {stats.get('posts', 'N/A')}\n"
                f"<b>Verified:</b> {'Yes ✅' if stats.get('is_verified') else 'No'}\n"
                f"<code>{BRAND_NAME}</code>"
            )
            await msg.delete()
            await update.message.reply_photo(photo=fb, caption=cap, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed photo gen: {e}")
            await msg.edit_text(f"✅ <b>@{u}</b> is <b>ACTIVE</b>\n<b>Followers:</b> {fmt_num(stats.get('followers', 0))}\n<code>{BRAND_NAME}</code>", parse_mode=ParseMode.HTML)
    else:
        await msg.edit_text(f"⚠️ Could not check @{u}. Try again later.")

async def watching_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = context.bot.token
    accounts = db_load_accounts(token)
    posts = db_load_posts(token)
    
    al = [f"• @{a['username']} ({a['mode']})" for a in accounts]
    pl = [f"• <code>{p['shortcode']}</code> ({p['mode']})" for p in posts]
    
    if not al and not pl:
        await update.message.reply_text("Nothing monitored under this bot instance.")
        return
    
    m = ["<b>👁️ Currently Watching</b>\n"]
    if al: m.append("<b>Accounts:</b>"); m.extend(al); m.append("")
    if pl: m.append("<b>Posts:</b>"); m.extend(pl); m.append("")
    m.append(f"<code>{BRAND_NAME}</code>")
    await update.message.reply_text("\n".join(m), parse_mode=ParseMode.HTML)

# ============================================================
# MODERN MONITOR SWEEP ENGINE (Asynchronous)
# ============================================================

async def monitor_loop():
    """Asynchronous infinite loops across dynamic items loaded from database"""
    logger.info("Background structural engine monitor loop active.")
    while True:
        try:
            accounts = db_load_accounts()
            posts = db_load_posts()
            
            if not accounts and not posts:
                await asyncio.sleep(CHECK_INTERVAL)
                continue
                
            async with aiohttp.ClientSession() as session:
                for acc in accounts:
                    token = acc["bot_token"]
                    if token not in RUNNING_BOTS: continue
                    bot = RUNNING_BOTS[token].bot
                    
                    try:
                        stats = await get_acc_stats(session, acc["username"])
                        fired = None
                        if stats.get("status") == 404 and acc["mode"] == "remove":
                            fired = "removed"
                        elif stats.get("status") == 200 and acc["mode"] == "recover":
                            fired = "recovered"
                            
                        if fired:
                            us = stats if fired == "recovered" else acc["last"]
                            cap = await acc_caption(fired, acc["username"], us, acc["start"])
                            try:
                                fb = await gen_screenshot(acc["username"], us)
                                await bot.send_photo(chat_id=acc["chat"], photo=fb, caption=cap, parse_mode=ParseMode.HTML)
                            except Exception:
                                try:
                                    await bot.send_message(chat_id=acc["chat"], text=cap, parse_mode=ParseMode.HTML)
                                except Exception as ex:
                                    logger.error(f"Failed sending backup alert: {ex}")
                            db_remove_account(token, acc["username"])
                    except Exception as e:
                        logger.error(f"Sweep account task error: {e}")
                    await asyncio.sleep(PER_TARGET_DELAY)
                    
                for p in posts:
                    token = p["bot_token"]
                    if token not in RUNNING_BOTS: continue
                    bot = RUNNING_BOTS[token].bot
                    
                    try:
                        stats = await get_post_stats(session, p["url"])
                        fired = None
                        if stats.get("status") == 404 and p["mode"] == "remove":
                            fired = "removed"
                        elif stats.get("status") == 200 and p["mode"] == "recover":
                            fired = "recovered"
                            
                        if fired:
                            us = stats if fired == "recovered" else p["last"]
                            cap = post_caption(fired, p["shortcode"], p["url"], us, p["start"])
                            try:
                                await bot.send_message(chat_id=p["chat"], text=cap, parse_mode=ParseMode.HTML)
                            except Exception as ex:
                                logger.error(f"Failed sending post alert: {ex}")
                            db_remove_post(token, p["shortcode"])
                    except Exception as e:
                        logger.error(f"Sweep post task error: {e}")
                    await asyncio.sleep(PER_TARGET_DELAY)
                    
        except Exception as e:
            logger.error(f"Critical routine loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

# ============================================================
# UNIFIED APPLICATION INITIALIZATION
# ============================================================

def setup_handlers(app: Application):
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("recover", recover_cmd))
    app.add_handler(CommandHandler("postremove", postremove_cmd))
    app.add_handler(CommandHandler("postrecover", postrecover_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("watching", watching_cmd))

async def main():
    logger.info("Starting structural multi-bot framework loop context.")
    init_db()
    
    if BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE":
        save_bot_config(BOT_TOKEN, ADMIN_IDS)
        
    configs = load_all_bot_configs()
    if not configs:
        logger.error("No valid bot configs discovered inside SQLite database.")
        return
        
    for cfg in configs:
        token = cfg["token"]
        try:
            app = Application.builder().token(token).build()
            setup_handlers(app)
            await app.initialize()
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            await app.start()
            RUNNING_BOTS[token] = app
            logger.info(f"Bot successfully activated: {token[:15]}...")
        except Exception as e:
            logger.error(f"Failed starting instance {token[:15]}... Error: {e}")

    # Inject background task directly into the execution loop context
    asyncio.create_task(monitor_loop())
    
    # Keep operational context open permanently
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested. Application closed cleanly.")