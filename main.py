print("--- INSTA BOT INITIALIZING ---")
import asyncio
import logging
import os
import re
from pathlib import Path
from datetime import datetime

import aiosqlite
import instaloader
import yt_dlp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    FSInputFile, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# ─────────────────────────────────────────────
#  SOZLAMALAR
# ─────────────────────────────────────────────
VERSION = "2.7 (MP3 + Video Buttons)"
load_dotenv()
BOT_TOKEN  = os.getenv("INSTA_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@Shadowstrike777")
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "+998907677622")


if not BOT_TOKEN:
    raise ValueError(".env faylida TELEGRAM_BOT_TOKEN yo'q!")

BASE_DIR     = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DB_PATH      = BASE_DIR / "bot.db"
DOWNLOAD_DIR.mkdir(exist_ok=True)
COOKIES_FILE = BASE_DIR / "cookies.txt"


# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  BOT
# ─────────────────────────────────────────────
if BOT_TOKEN:
    masked_token = BOT_TOKEN[:5] + "..." + BOT_TOKEN[-5:]
    log.info(f"Bot token yuklandi: {masked_token}")
else:
    log.error("BOT_TOKEN TOPILMADI!")

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

@dp.message(Command("ping"))
async def ping(message: types.Message):
    await message.answer("pong! Men tirikman ✅")

@dp.message(Command("test_yt"))
async def test_yt(message: types.Message):
    await message.answer("YouTube tekshirilmoqda...")
    try:
        ydl_opts = {'quiet': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info("ytsearch1:test", download=False)
            title = info['entries'][0]['title']
            await message.answer(f"✅ YouTube ulandi! Topilgan video: {title}")
    except Exception as e:
        await message.answer(f"❌ YouTube xatosi: {str(e)[:100]}")

# ─────────────────────────────────────────────
#  MA'LUMOTLAR BAZASI
# ─────────────────────────────────────────────
async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER UNIQUE NOT NULL,
                username   TEXT,
                full_name  TEXT,
                joined_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                platform   TEXT NOT NULL,
                url        TEXT NOT NULL,
                status     TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.commit()
    log.info("Ma'lumotlar bazasi tayyor.")


async def db_save_user(user: types.User):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name
        """, (user.id, user.username, user.full_name))
        await db.commit()


async def db_save_download(user_id: int, platform: str, url: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO downloads (user_id, platform, url, status)
            VALUES (?, ?, ?, ?)
        """, (user_id, platform, url, status))
        await db.commit()


async def db_get_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        # Umumiy yuklanishlar
        cur = await db.execute(
            "SELECT COUNT(*) FROM downloads WHERE user_id=? AND status='ok'", (user_id,)
        )
        total = (await cur.fetchone())[0]

        # Platformalar bo'yicha
        cur = await db.execute("""
            SELECT platform, COUNT(*) FROM downloads
            WHERE user_id=? AND status='ok'
            GROUP BY platform
        """, (user_id,))
        platforms = dict(await cur.fetchall())

    return {"total": total, "platforms": platforms}


async def db_get_global_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        users = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM downloads WHERE status='ok'")
        total = (await cur.fetchone())[0]

        cur = await db.execute("""
            SELECT platform, COUNT(*) FROM downloads
            WHERE status='ok' GROUP BY platform
        """)
        platforms = dict(await cur.fetchall())

    return {"users": users, "total": total, "platforms": platforms}


# ─────────────────────────────────────────────
#  KLAVIATURA (pastda doim turadigan tugmalar)
# ─────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📊 Statistikam"),
            KeyboardButton(text="ℹ️ Yordam"),
        ],
        [
            KeyboardButton(text="Tozalash 🗑️"),
        ],
    ],
    resize_keyboard=True,      # Tugmalarni kichikroq qiladi
    is_persistent=True,        # Har doim pastda tuради
    input_field_placeholder="Link yuboring..."
)


# ─────────────────────────────────────────────
#  INSTALOADER
# ─────────────────────────────────────────────
L = instaloader.Instaloader(
    dirname_pattern=str(DOWNLOAD_DIR / "{shortcode}"),
    filename_pattern="{shortcode}",
    download_pictures=False,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    post_metadata_txt_pattern="",
)

if IG_USERNAME and IG_PASSWORD and IG_USERNAME != "Sizning_Instagram_Ismingiz":
    try:
        L.login(IG_USERNAME, IG_PASSWORD)
        log.info(f"Instagram ga kirildi: {IG_USERNAME}")
    except Exception as e:
        log.warning(f"Instagram ga kirib bo'lmadi: {e}")

INSTA_RE   = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
YOUTUBE_RE = re.compile(r"(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[^\s]+)")
TIKTOK_RE  = re.compile(r"(https?://(?:www\.|vm\.)?tiktok\.com/[^\s]+)")


def get_insta_shortcode(text: str): return (m := INSTA_RE.search(text)) and m.group(1)
def get_youtube_url(text: str):     return (m := YOUTUBE_RE.search(text)) and m.group(1)
def get_tiktok_url(text: str):      return (m := TIKTOK_RE.search(text)) and m.group(1)


async def download_instagram(shortcode: str) -> Path | None:
    try:
        loop = asyncio.get_event_loop()
        def _dl():
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            if not post.is_video:
                raise ValueError("Bu post video emas!")
            L.download_post(post, target=DOWNLOAD_DIR / shortcode)
        await loop.run_in_executor(None, _dl)

        videos = list((DOWNLOAD_DIR / shortcode).glob("*.mp4"))
        return videos[0] if videos else None
    except Exception as e:
        log.error(f"Instagram xatolik [{shortcode}]: {e}")
        return None


async def download_ytdlp(url: str, output_path: Path) -> Path | None:
    """
    Android client orqali yuklab olish — tez va kuki talab qilmaydi.
    """
    ydl_opts = {
        "outtmpl": str(output_path.with_suffix("")) + ".%(ext)s",
        "format": "best[ext=mp4][filesize<50M]/best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # Bir nechta klientlardan foydalanish (android, ios, web_embedded) blokdan o'tishga yordam beradi
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "web_embedded"],
                "skip": ["dash", "hls"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-us,en;q=0.5",
        }
    }

    # Agar cookies.txt mavjud bo'lsa, ishlatish
    if COOKIES_FILE.exists():
        ydl_opts["cookiefile"] = str(COOKIES_FILE)
        log.info("cookies.txt ishlatilmoqda...")

    try:
        loop = asyncio.get_event_loop()
        def _dl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        await loop.run_in_executor(None, _dl)

        for ext in ["mp4", "mkv", "webm"]:
            c = output_path.with_suffix(f".{ext}")
            if c.exists():
                log.info(f"Video topildi: {c}")
                return c

        log.error("Video fayl topilmadi!")
        return None
    except Exception as e:
        log.error(f"yt-dlp xatolik: {e}")
        return None

async def download_cobalt(url: str) -> Path | None:
    """
    Cobalt API orqali yuklab olish — juda barqaror va tez.
    """
    api_url = "https://api.cobalt.tools/"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    payload = {
        "url": url,
        "videoQuality": "720",
        "filenameStyle": "classic",
        "isAudioOnly": False,
        "downloadMode": "auto"
    }

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    log.error(f"Cobalt API xatosi: {resp.status}")
                    return None
                
                data = await resp.json()
                if data.get("status") == "error":
                    log.error(f"Cobalt xatosi: {data.get('text')}")
                    return None
                
                stream_url = data.get("url")
                if not stream_url:
                    log.error("Cobalt stream URL topilmadi")
                    return None

                # Faylni yuklab olish
                filename = f"cobalt_{datetime.now().timestamp()}.mp4"
                file_path = DOWNLOAD_DIR / filename
                
                async with session.get(stream_url) as file_resp:
                    if file_resp.status == 200:
                        with open(file_path, "wb") as f:
                            f.write(await file_resp.read())
                        return file_path
    except Exception as e:
        log.error(f"Cobalt download error: {e}")
    return None


async def download_music(query: str) -> str | None:
    """
    Invidious orqali qidirib, YouTube URL sini topish va audio URL sini qaytarish.
    Bu YouTube blokidan o'tishning eng ishonchli usuli.
    """
    try:
        # 1. Invidious API orqali qidirish
        search_url = f"https://inv.tux.rs/api/v1/search?q={query}"
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        video_id = data[0].get("videoId")
                        if video_id:
                            yt_url = f"https://www.youtube.com/watch?v={video_id}"
                            
                            # 2. Endi bu URL dan audio linkini olamiz
                            ydl_opts = {
                                'format': 'bestaudio/best',
                                'quiet': True,
                                'nocheckcertificate': True,
                                'extractor_args': {'youtube': {'player_client': ['tv', 'web']}}
                            }
                            loop = asyncio.get_event_loop()
                            def _get_info():
                                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                                    return ydl.extract_info(yt_url, download=False)
                            
                            info = await loop.run_in_executor(None, _get_info)
                            if info and 'url' in info:
                                return info['url']
    except Exception as e:
        log.error(f"Invidious/Music xatolik: {e}")
    return None


# ─────────────────────────────────────────────
#  UNIVERSAL YUKLASH YORDAMCHISI
# ─────────────────────────────────────────────
async def send_audio(message: types.Message, audio_url: str, title: str):
    try:
        # Telegram audio URL orqali ham fayl yubora oladi (50MB gacha)
        await message.reply_audio(
            audio=audio_url,
            caption=f"🎵 <b>{title}</b>\n\n✅ Tayyor! (Super Speed)",
            parse_mode="HTML"
        )
        await db_save_download(message.from_user.id, "Music", title, "ok")
    except Exception as e:
        log.error(f"Audio yuborishda xatolik: {e}")
        # Agar URL orqali yuborish o'xshamasa, foydalanuvchiga xabar beramiz
        await message.reply("❌ Audioni yuborishda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")
async def send_video(message: types.Message, video_path: Path, url: str, platform: str):
    try:
        # Inline tugmalar qo'shish
        shortcode = get_insta_shortcode(url) if platform == "Instagram" else None
        
        kb_list = []
        if platform == "Instagram" and shortcode:
            kb_list.append([
                InlineKeyboardButton(text="🎵 MP3 yuklash", callback_data=f"dl_mp3:ig:{shortcode}"),
                InlineKeyboardButton(text="🎥 Video", callback_data=f"dl_vid:ig:{shortcode}")
            ])
        elif platform == "YouTube":
            # YouTube uchun url ni qisqartirib yuboramiz (faqat ID)
            video_id = url.split("v=")[-1].split("&")[0] if "v=" in url else url.split("/")[-1]
            kb_list.append([
                InlineKeyboardButton(text="🎵 MP3 yuklash", callback_data=f"dl_mp3:yt:{video_id}")
            ])

        reply_markup = InlineKeyboardMarkup(inline_keyboard=kb_list) if kb_list else None

        await message.reply_video(
            video=FSInputFile(str(video_path)),
            caption=f"✅ Mana! ({platform})",
            reply_markup=reply_markup
        )
        await db_save_download(message.from_user.id, platform, url, "ok")
        log.info(f"{platform} video yuborildi → user {message.from_user.id}")
    except Exception as e:
        log.error(f"Yuborishda xatolik: {e}")
        await message.reply("❌ Video yuborishda xatolik yuz berdi.")
        await db_save_download(message.from_user.id, platform, url, "error")


# ─────────────────────────────────────────────
#  HANDLERLAR
# ─────────────────────────────────────────────
@dp.message(CommandStart())
async def start(message: types.Message):
    log.info(f"Start buyrug'i: (User: {message.from_user.id})")
    await db_save_user(message.from_user)
    await message.answer(
        f"Assalomu alaykum, <b>{message.from_user.full_name}</b>! 👋\n"
        f"<i>Bot versiyasi: {VERSION}</i>\n\n"
        "📥 <b>Quyidagi platformalardan link yuboring:</b>\n"
        "• 📸 Instagram — Reel / Post\n"
        "• ▶️ YouTube — Video / Shorts / Music\n"
        "• 🎵 TikTok — Video\n\n"
        "Men videoni yuklab beraman!",
        parse_mode="HTML",
        reply_markup=MAIN_KB
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    log.info(f"Stats buyrug'i: (User: {message.from_user.id})")
    await db_save_user(message.from_user)
    stats = await db_get_stats(message.from_user.id)
    p = stats["platforms"]
    text = (
        f"📊 <b>Sizning statistikangiz</b>\n\n"
        f"Jami yuklandi: <b>{stats['total']}</b> ta video\n"
        f"├ 📸 Instagram: <b>{p.get('Instagram', 0)}</b>\n"
        f"├ ▶️ YouTube:   <b>{p.get('YouTube', 0)}</b>\n"
        f"└ 🎵 TikTok:   <b>{p.get('TikTok', 0)}</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=MAIN_KB)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    log.info(f"Help buyrug'i: (User: {message.from_user.id})")
    await message.answer(
        "ℹ️ <b>Yordam va Bog'lanish</b>\n\n"
        "Menga shunchaki video linkini yuboring yoki qo'shiq nomini yozing.\n\n"
        f"👤 <b>Admin:</b> {ADMIN_USERNAME}\n"
        f"📞 <b>Tel:</b> {ADMIN_PHONE}\n\n"
        "📸 <b>Instagram:</b> Reel / Post\n"
        "▶️ <b>YouTube:</b> Video / Shorts / Music\n"
        "🎵 <b>TikTok:</b> Video\n\n"
        "📌 <b>Eslatma:</b> 50MB dan katta videolar yuklanmaydi.",
        parse_mode="HTML",
        reply_markup=MAIN_KB
    )


# ─── Callback tugmalar ───
@dp.callback_query(F.data == "my_stats")
async def cb_stats(call: CallbackQuery):
    stats = await db_get_stats(call.from_user.id)
    p = stats["platforms"]
    text = (
        f"📊 <b>Sizning statistikangiz</b>\n\n"
        f"Jami yuklandi: <b>{stats['total']}</b> ta video\n"
        f"├ 📸 Instagram: <b>{p.get('Instagram', 0)}</b>\n"
        f"├ ▶️ YouTube:   <b>{p.get('YouTube', 0)}</b>\n"
        f"└ 🎵 TikTok:   <b>{p.get('TikTok', 0)}</b>"
    )
    await call.answer()
    await call.message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    await call.answer()
    await call.message.answer(
        "ℹ️ <b>Yordam va Bog'lanish</b>\n\n"
        "Menga shunchaki video linkini yuboring yoki qo'shiq nomini yozing.\n\n"
        f"👤 <b>Admin:</b> {ADMIN_USERNAME}\n"
        f"📞 <b>Tel:</b> {ADMIN_PHONE}\n\n"
        "📸 <b>Instagram:</b> Reel / Post\n"
        "▶️ <b>YouTube:</b> Video / Shorts / Music\n"
        "🎵 <b>TikTok:</b> Video\n\n"
        "📌 <b>Eslatma:</b> 50MB dan katta videolar yuklanmaydi.",
        parse_mode="HTML"
    )


# ─── Link handler ───
@dp.callback_query(F.data.startswith("dl_"))
async def cb_download_more(call: CallbackQuery):
    data = call.data.split(":")
    action = data[0] # dl_mp3 yoki dl_vid
    platform = data[1] # ig yoki yt
    identifier = data[2] # shortcode yoki id
    
    url = ""
    if platform == "ig":
        url = f"https://www.instagram.com/reel/{identifier}/"
    elif platform == "yt":
        url = f"https://www.youtube.com/watch?v={identifier}"
    
    await call.answer("⏳ Tayyorlanmoqda...")
    wait_msg = await call.message.answer("⏳ Yuklanmoqda, kuting...")
    
    try:
        if action == "dl_mp3":
            # download_music URL bilan ham ishlaydi
            audio_url = await download_music(url)
            if audio_url:
                await send_audio(call.message, audio_url, f"{platform.upper()} Audio")
            else:
                await call.message.answer("❌ Audioni yuklab bo'lmadi.")
        
        elif action == "dl_vid":
            video_path = await download_cobalt(url)
            if video_path and video_path.exists():
                await call.message.reply_video(FSInputFile(str(video_path)), caption="✅ Mana video!")
                video_path.unlink(missing_ok=True)
            else:
                await call.message.answer("❌ Videoni yuklab bo'lmadi.")
    except Exception as e:
        log.error(f"Callback download error: {e}")
        await call.message.answer("❌ Xatolik yuz berdi.")
        
    try:
        await bot.delete_message(call.message.chat.id, wait_msg.message_id)
    except: pass

@dp.message(F.text)
async def handle_link(message: types.Message):
    await db_save_user(message.from_user)
    text = message.text.strip()
    log.info(f"Yangi xabar: {text} (User: {message.from_user.id})")

    shortcode  = get_insta_shortcode(text)
    youtube_url = get_youtube_url(text)
    tiktok_url  = get_tiktok_url(text)
    
    log.info(f"Shortcode: {shortcode}, YT: {youtube_url}, TT: {tiktok_url}")

    if not shortcode and not youtube_url and not tiktok_url:
        # Tugma bosilganini tekshirish
        if text == "📊 Statistikam":
            stats = await db_get_stats(message.from_user.id)
            p = stats["platforms"]
            await message.answer(
                f"📊 <b>Sizning statistikangiz</b>\n\n"
                f"Jami yuklandi: <b>{stats['total']}</b> ta video\n"
                f"├ 📸 Instagram: <b>{p.get('Instagram', 0)}</b>\n"
                f"├ ▶️ YouTube:   <b>{p.get('YouTube', 0)}</b>\n"
                f"└ 🎵 TikTok:   <b>{p.get('TikTok', 0)}</b>",
                parse_mode="HTML", reply_markup=MAIN_KB
            )
            return
        if text == "ℹ️ Yordam":
            await message.answer(
                "ℹ️ <b>Yordam va Bog'lanish</b>\n\n"
                "Menga shunchaki video linkini yuboring yoki qo'shiq nomini yozing.\n\n"
                f"👤 <b>Admin:</b> {ADMIN_USERNAME}\n"
                f"📞 <b>Tel:</b> {ADMIN_PHONE}\n\n"
                "📸 <b>Instagram:</b> Reel / Post\n"
                "▶️ <b>YouTube:</b> Video / Shorts / Music\n"
                "🎵 <b>TikTok:</b> Video\n\n"
                "📌 <b>Eslatma:</b> 50MB dan katta videolar yuklanmaydi.",
                parse_mode="HTML", reply_markup=MAIN_KB
            )
            return
        if text == "Tozalash 🗑️":
            await message.answer("⏳ Chat tozalanmoqda...")
            for i in range(message.message_id, message.message_id - 100, -1):
                try:
                    await bot.delete_message(message.chat.id, i)
                except:
                    continue
            return
        
        # Agar link bo'lmasa, musiqa deb qidiramiz
        if message.chat.type == "private":
            wait_msg = await message.reply(f"🔍 <b>'{text}'</b> qidirilmoqda...", parse_mode="HTML")
            try:
                audio_url = await download_music(text)
                if audio_url:
                    await send_audio(message, audio_url, text)
                else:
                    await message.reply("❌ Hech narsa topilmadi.")
            except Exception as e:
                await message.reply(f"❌ Xatolik yuz berdi: {str(e)[:50]}")
            
            try:
                await bot.delete_message(message.chat.id, wait_msg.message_id)
            except: pass
        return

    # Linklarni tekshirish
    if shortcode or youtube_url or tiktok_url:
        wait_msg = await message.reply("⏳ Video yuklanmoqda, kuting...")

    # ── Instagram ──
    if shortcode:
        # Avval Cobalt orqali urinib ko'ramiz
        video_path = await download_cobalt(text)
        if not video_path:
            # Agar bo'lmasa instaloader (eski usul)
            video_path = await download_instagram(shortcode)
        
        if video_path and video_path.exists():
            await send_video(message, video_path, text, "Instagram")
            # Cobalt fayllarini o'chirish
            if "cobalt_" in video_path.name:
                video_path.unlink(missing_ok=True)
        else:
            await message.reply("❌ Instagram videoini yuklab bo'lmadi.\nVideo yopiq profilga tegishli bo'lishi mumkin.")
            await db_save_download(message.from_user.id, "Instagram", text, "error")
        
        # Instaloader papkasini tozalash
        folder = DOWNLOAD_DIR / shortcode
        if folder.exists():
            for f in folder.glob("*"): f.unlink(missing_ok=True)
            try: folder.rmdir()
            except: pass

    # ── YouTube ──
    elif youtube_url:
        # Avval Cobalt
        video_path = await download_cobalt(youtube_url)
        if not video_path:
            # Zaxira: yt-dlp
            filename = f"yt_{message.from_user.id}_{message.message_id}.mp4"
            video_path = await download_ytdlp(youtube_url, DOWNLOAD_DIR / filename)
        
        if video_path and video_path.exists():
            await send_video(message, video_path, youtube_url, "YouTube")
            video_path.unlink(missing_ok=True)
        else:
            await message.reply("❌ YouTube videoini yuklab bo'lmadi.")
            await db_save_download(message.from_user.id, "YouTube", youtube_url, "error")

    # ── TikTok ──
    elif tiktok_url:
        video_path = await download_cobalt(tiktok_url)
        if not video_path:
            filename = f"tt_{message.from_user.id}_{message.message_id}.mp4"
            video_path = await download_ytdlp(tiktok_url, DOWNLOAD_DIR / filename)
            
        if video_path and video_path.exists():
            await send_video(message, video_path, tiktok_url, "TikTok")
            video_path.unlink(missing_ok=True)
        else:
            await message.reply("❌ TikTok videoini yuklab bo'lmadi.")
            await db_save_download(message.from_user.id, "TikTok", tiktok_url, "error")

    try:
        await bot.delete_message(message.chat.id, wait_msg.message_id)
    except: pass


# ─────────────────────────────────────────────
#  KEEP-ALIVE SERVER (Render uchun)
# ─────────────────────────────────────────────
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def main():
    # Flask serverni alohida oqimda ishga tushirish
    Thread(target=run_flask).start()
    
    if not BOT_TOKEN:
        log.error("Bot ishga tushmadi: Token yo'q. Render'da INSTA_BOT_TOKEN ni tekshiring.")
        return
    try:
        await db_init()
        log.info("Instagram + YouTube + TikTok bot ishga tushdi!")
        # Delete webhook before polling to avoid conflicts
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        log.critical(f"Bot ishga tushishida xatolik: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot to'xtatildi.")
    except Exception as e:
        log.error(f"Kutilmagan xatolik: {e}")
