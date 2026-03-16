import os
import asyncio
import logging
import sqlite3
import secrets
import aiohttp
import aiofiles
import re
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================= Configuration =================
API_ID = int(os.getenv("API_ID", "1234567")) 
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH") 
TERABOX_BOT_TOKEN = os.getenv("TERABOX_BOT_TOKEN", "YOUR_TERABOX_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-100YOUR_CHANNEL_ID_HERE")) 
XAPI_KEY = os.getenv("XAPI_KEY", "YOUR_XAPIVERSE_KEY")
FILESHARE_BOT_USERNAME = os.getenv("FILESHARE_BOT_USERNAME", "FSB69_BOT") 

TEMP_MSG_DELETE_TIME = 120    
FILE_DELETE_TIME = 3600       

logging.basicConfig(level=logging.INFO)

app = Client(
    "terabox_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=TERABOX_BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML
)

active_welcome_msgs = {}

# ================= Database Setup =================
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS shared_files (link_id TEXT, message_id INTEGER)')
cursor.execute('CREATE TABLE IF NOT EXISTS terabox_cache (terabox_url TEXT PRIMARY KEY, message_id INTEGER)')
conn.commit()

# ================= Utility Functions =================
async def safe_delete(message):
    try: await message.delete()
    except Exception: pass

async def delete_after(client, chat_id, message_id, delay):
    await asyncio.sleep(delay)
    try: await client.delete_messages(chat_id, message_id)
    except Exception: pass

# 🥷 THE FAST LINK UNSHORTENER (Now with Firewall Bypass Headers)
async def resolve_redirect(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, allow_redirects=True, timeout=15) as resp:
                return str(resp.url)
    except Exception as e:
        print(f"Redirect Resolve Error: {e}")
        return url

# ================= Bot Logic =================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    await safe_delete(message)
    msg = await message.reply_text("<blockquote>✨ <b>Transmit a Terabox Link</b> 🙌\n<i>Our servers will handle the rest.</i></blockquote>")
    active_welcome_msgs[message.chat.id] = msg.id 
    asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))

@app.on_callback_query(filters.regex("terabox_start"))
async def callback_download_more(client, callback_query):
    msg = await callback_query.message.reply_text("<blockquote>✨ <b>Transmit a Terabox Link</b> 🙌\n<i>Ready for the next payload.</i></blockquote>")
    active_welcome_msgs[callback_query.message.chat.id] = msg.id 
    await callback_query.answer()

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def process_terabox_link(client, message):
    chat_id = message.chat.id
    raw_text = message.text
    text = raw_text.lower()
    
    if chat_id in active_welcome_msgs:
        try:
            await client.delete_messages(chat_id, active_welcome_msgs[chat_id])
            del active_welcome_msgs[chat_id]
        except Exception:
            pass
            
    valid_domains = [
        "terabox", "1024tera", "1024terabox", "terashare", "4funbox", 
        "mirrobox", "nephobox", "freeterabox", "momerybox", "teraboxapp"
    ]
    
    if not any(domain in text for domain in valid_domains):
        await safe_delete(message)
        err = await message.reply_text("<blockquote>⚠️ <b>Invalid protocol.</b>\nRequires a valid Terabox family URL.</blockquote>")
        asyncio.create_task(delete_after(client, err.chat.id, err.id, TEMP_MSG_DELETE_TIME))
        return

    await safe_delete(message)
    
    url_match = re.search(r"https?://[^\s]+", raw_text, re.IGNORECASE)
    if not url_match:
        err = await message.reply_text("<blockquote>❌ <b>Extraction Failed.</b> No valid URL detected.</blockquote>")
        asyncio.create_task(delete_after(client, err.chat.id, err.id, TEMP_MSG_DELETE_TIME))
        return
        
    short_url = url_match.group(0)
    
    await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
    anim_msg = await message.reply_text("<blockquote><code>[🔍] Fetching...</code></blockquote>")

    # 🥷 1. UNSHORTEN THE LINK FIRST
    clean_url = await resolve_redirect(short_url)
    print(f"Processing URL -> Original: {short_url} | Resolved: {clean_url}")
    
    # ================= CACHE CHECK =================
    cursor.execute('SELECT message_id FROM terabox_cache WHERE terabox_url = ?', (clean_url,))
    cached_result = cursor.fetchone()
    
    if cached_result:
        msg_id = cached_result[0]
        link_id = secrets.token_urlsafe(8)
        cursor.execute('INSERT INTO shared_files (link_id, message_id) VALUES (?, ?)', (link_id, msg_id))
        conn.commit()
        
        orig_msg = await client.get_messages(CHANNEL_ID, msg_id)
        vid = orig_msg.video or orig_msg.document
        
        file_name = getattr(vid, "file_name", "terabox_video.mp4")
        dur_secs = getattr(vid, "duration", 0)
        dur_str = f"{dur_secs // 60:02d}:{dur_secs % 60:02d}" if dur_secs else "Unknown"
        file_size = getattr(vid, "file_size", 0)
        size_mb = f"{file_size / (1024 * 1024):.2f} MB" if file_size else "Unknown"

        icon = "🎬" if orig_msg.video else "📄"
        user_caption = (
            f"{icon} <b>{file_name}</b>\n\n"
            f"⏱ <b>Duration:</b> {dur_str}\n"
            f"📦 <b>Size:</b> {size_mb}\n\n"
            f"⚠️ <b>Note:</b> File will be auto-deleted after {FILE_DELETE_TIME // 3600} hour(s)"
        )

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download More", callback_data="terabox_start")]])
        sent_vid = await client.copy_message(
            chat_id=message.chat.id, 
            from_chat_id=CHANNEL_ID, 
            message_id=msg_id, 
            caption=user_caption, 
            reply_markup=keyboard
        )
        active_welcome_msgs[message.chat.id] = sent_vid.id
        await safe_delete(anim_msg)
        asyncio.create_task(delete_after(client, message.chat.id, sent_vid.id, FILE_DELETE_TIME))
        return 
    # ==================================================================

    api_url = 'https://xapiverse.com/api/terabox-pro'
    headers = {'Content-Type': 'application/json', 'xAPIverse-Key': XAPI_KEY}
    payload = {"url": clean_url} 
    timeout = aiohttp.ClientTimeout(total=3600) 
    
    video_url = None
    thumb_url = None
    file_name = "terabox_video.mp4"
    duration_str = "Unknown"
    size_fmt = "Unknown"
    
    api_success = False

    # 🥷 2. BULLETPROOF 3-ATTEMPT RETRY LOOP
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_url, json=payload, headers=headers) as resp:
                    data = await resp.json()
                    if data.get("status") == "success" and data.get("list"):
                        file_data = data["list"][0]
                        
                        # 🛡️ THE FIX: Safely parse the stream dictionary to prevent silent NoneType crashes
                        streams = file_data.get("fast_stream_url")
                        video_url = None
                        
                        if isinstance(streams, dict):
                            video_url = streams.get("1080p") or streams.get("720p") or streams.get("480p") or streams.get("360p")
                        elif isinstance(streams, str) and streams.startswith("http"):
                            video_url = streams
                        
                        # Extreme fallback if stream array is empty or None
                        if not video_url:
                            video_url = file_data.get("stream_url") or file_data.get("fast_download_link") or file_data.get("download_link")
                        
                        thumb_url = file_data.get("thumbnail")
                        file_name = file_data.get("name", "terabox_video.mp4")
                        duration_str = file_data.get("duration", "00:00")
                        size_fmt = file_data.get("size_formatted", "Unknown")
                        
                        api_success = True
                        break 
                    else:
                        print(f"API Attempt {attempt + 1} Failed: {data}")
        except Exception as e:
            print(f"API Attempt {attempt + 1} Exception: {e}")
        
        if not api_success:
            await asyncio.sleep(2)

    if not api_success or not video_url:
        await anim_msg.edit_text("<blockquote>❌ <b>Extraction Failed.</b> The file is unavailable.</blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        return

    dur_parts = duration_str.split(":")
    dur_secs = 0
    if len(dur_parts) == 2: dur_secs = int(dur_parts[0]) * 60 + int(dur_parts[1])
    elif len(dur_parts) == 3: dur_secs = int(dur_parts[0]) * 3600 + int(dur_parts[1]) * 60 + int(dur_parts[2])

    # 🥷 3. CLEAN DOWNLOADING STATUS
    await anim_msg.edit_text("<blockquote><code>[📥] Downloading...</code></blockquote>")
    await client.send_chat_action(message.chat.id, enums.ChatAction.RECORD_VIDEO)
    
    os.makedirs("downloads", exist_ok=True)
    local_filename = f"downloads/{secrets.token_hex(4)}_{file_name}"
    thumb_path = f"downloads/thumb_{secrets.token_hex(4)}.jpg" if thumb_url else None
    
    try:
        dl_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        
        async with aiohttp.ClientSession(timeout=timeout, headers=dl_headers) as session:
            if thumb_url:
                async with session.get(thumb_url) as t_resp:
                    if t_resp.status == 200:
                        async with aiofiles.open(thumb_path, mode='wb') as f:
                            await f.write(await t_resp.read())
            
            stream_downloaded = False
            
            if ".m3u8" in video_url:
                try:
                    process = await asyncio.create_subprocess_exec(
                        'ffmpeg', '-i', video_url, '-c', 'copy', local_filename,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    await process.communicate()
                    # 1MB TRAP CHECK
                    if os.path.exists(local_filename) and os.path.getsize(local_filename) > 1024 * 1024:
                        stream_downloaded = True
                except Exception as e:
                    print(f"FFmpeg Error: {e}")
            
            if not stream_downloaded:
                async with session.get(video_url) as resp:
                    if resp.status == 200:
                        async with aiofiles.open(local_filename, mode='wb') as f:
                            while True:
                                chunk = await resp.content.read(2 * 1024 * 1024) 
                                if not chunk: break
                                await f.write(chunk)
                        
                        # 1MB TRAP CHECK
                        if os.path.getsize(local_filename) > 1024 * 1024:
                            stream_downloaded = True
                            
            if not stream_downloaded:
                raise Exception("Download blocked or file too small.")

    except Exception as e:
        print(f"Download Exception: {e}")
        await anim_msg.edit_text("<blockquote>❌ <b>Download Failed.</b> Please try again later.</blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        return

    # 🥷 4. CLEAN UPLOADING STATUS
    await anim_msg.edit_text("<blockquote><code>[📤] Uploading...</code></blockquote>")
    await client.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_VIDEO)

    link_id = secrets.token_urlsafe(8)
    fsb_link = f"https://t.me/{FILESHARE_BOT_USERNAME}?start={link_id}"
    channel_caption = f"🔗 **Access Link:**\n<code>{fsb_link}</code>"

    file_ext = file_name.split('.')[-1].lower() if '.' in file_name else 'mp4'
    video_extensions = ['mp4', 'mkv', 'webm', 'avi', 'mov', 'flv']

    try:
        if file_ext in video_extensions:
            saved_msg = await client.send_video(
                chat_id=CHANNEL_ID, 
                video=local_filename, 
                caption=channel_caption, 
                has_spoiler=True,
                duration=dur_secs,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                file_name=file_name,
                supports_streaming=True
            )
        else:
            saved_msg = await client.send_document(
                chat_id=CHANNEL_ID, 
                document=local_filename, 
                caption=channel_caption, 
                file_name=file_name
            )
        
        cursor.execute('INSERT INTO shared_files (link_id, message_id) VALUES (?, ?)', (link_id, saved_msg.id))
        cursor.execute('INSERT INTO terabox_cache (terabox_url, message_id) VALUES (?, ?)', (clean_url, saved_msg.id))
        conn.commit()

        icon = "🎬" if file_ext in video_extensions else "📄"
        user_caption = (
            f"{icon} <b>{file_name}</b>\n\n"
            f"⏱ <b>Duration:</b> {duration_str}\n"
            f"📦 <b>Size:</b> {size_fmt}\n\n"
            f"⚠️ <b>Note:</b> File will be auto-deleted after {FILE_DELETE_TIME // 3600} hour(s)"
        )
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download More", callback_data="terabox_start")]])
        sent_vid = await client.copy_message(
            chat_id=message.chat.id, 
            from_chat_id=CHANNEL_ID, 
            message_id=saved_msg.id, 
            caption=user_caption, 
            reply_markup=keyboard
        )
        active_welcome_msgs[message.chat.id] = sent_vid.id
        
        asyncio.create_task(delete_after(client, message.chat.id, sent_vid.id, FILE_DELETE_TIME))

    except Exception as e:
        print(f"Upload Exception: {e}")
        await anim_msg.edit_text("<blockquote>❌ <b>Upload Error.</b> Please try again later.</blockquote>")
    finally:
        if os.path.exists(local_filename): os.remove(local_filename)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        await safe_delete(anim_msg)

if __name__ == "__main__":
    print("Starting Terabox Bot...")
    app.run()
        
