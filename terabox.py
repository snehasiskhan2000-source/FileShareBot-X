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

TEMP_MSG_DELETE_TIME = 120    # 2 mins for bot messages
FILE_DELETE_TIME = 3600       # 1 hour for the actual video

logging.basicConfig(level=logging.INFO)

app = Client(
    "terabox_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=TERABOX_BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML
)

# --- THE FIX: Dictionary to track and delete welcome messages ---
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

# ================= Bot Logic =================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    await safe_delete(message)
    msg = await message.reply_text("<blockquote>✨ <b>Transmit a Terabox Link</b> 🙌\n<i>Our servers will handle the rest.</i></blockquote>")
    active_welcome_msgs[message.chat.id] = msg.id # Track message
    asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))

@app.on_callback_query(filters.regex("terabox_start"))
async def callback_download_more(client, callback_query):
    msg = await callback_query.message.reply_text("<blockquote>✨ <b>Transmit a Terabox Link</b> 🙌\n<i>Ready for the next payload.</i></blockquote>")
    active_welcome_msgs[callback_query.message.chat.id] = msg.id # Track message
    await callback_query.answer()

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def process_terabox_link(client, message):
    chat_id = message.chat.id
    raw_text = message.text
    text = raw_text.lower()
    
    # --- THE FIX: Clean up the old welcome message immediately ---
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
        
    clean_url = url_match.group(0)
    
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
        
        file_name = getattr(vid, "file_name", "terabox_file")
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
        asyncio.create_task(delete_after(client, message.chat.id, sent_vid.id, FILE_DELETE_TIME))
        return 
    # ==================================================================

    await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
    
    # Crystal Clean Animation Sequence
    anim_msg = await message.reply_text("<blockquote>✨ <b>Transmitting Link</b> 🙌\n<i>Ready for payload.</i></blockquote>")
    await asyncio.sleep(0.4)
    await anim_msg.edit_text("<blockquote>✨ <b>Transmitting Link</b> 🙌\n<i>Ready for payload..</i></blockquote>")
    await asyncio.sleep(0.4)
    await anim_msg.edit_text("<blockquote>✨ <b>Transmitting Link</b> 🙌\n<i>Ready for payload...</i></blockquote>")
    await asyncio.sleep(0.4)
    await anim_msg.edit_text("<blockquote><code>[📡] Pinging servers...</code></blockquote>")

    api_url = 'https://xapiverse.com/api/terabox-pro'
    headers = {'Content-Type': 'application/json', 'xAPIverse-Key': XAPI_KEY}
    payload = {"url": clean_url} 
    
    video_url, thumb_url, file_name, duration_str, size_fmt = None, None, "terabox_file", "Unknown", "Unknown"
    timeout = aiohttp.ClientTimeout(total=3600) 
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if data.get("status") == "success" and data.get("list"):
                    file_data = data["list"][0]
                    video_url = file_data.get("fast_download_link") or file_data.get("download_link")
                    thumb_url = file_data.get("thumbnail")
                    file_name = file_data.get("name", "terabox_file")
                    duration_str = file_data.get("duration", "00:00")
                    size_fmt = file_data.get("size_formatted", "Unknown")
                else:
                    raise Exception(data.get("message", "API returned an empty list."))
    except Exception as e:
        await anim_msg.edit_text(f"<blockquote>❌ <b>API Error:</b>\n<code>{e}</code></blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        return

    if not video_url:
        await anim_msg.edit_text("<blockquote>❌ <b>Extraction Failed.</b> Link dead or private.</blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        return

    dur_parts = duration_str.split(":")
    dur_secs = 0
    if len(dur_parts) == 2: dur_secs = int(dur_parts[0]) * 60 + int(dur_parts[1])
    elif len(dur_parts) == 3: dur_secs = int(dur_parts[0]) * 3600 + int(dur_parts[1]) * 60 + int(dur_parts[2])

    await anim_msg.edit_text("<blockquote><code>[📥] Downloading...</code></blockquote>")
    await client.send_chat_action(message.chat.id, enums.ChatAction.RECORD_VIDEO)
    
    os.makedirs("downloads", exist_ok=True)
    local_filename = f"downloads/{secrets.token_hex(4)}_{file_name}"
    thumb_path = f"downloads/thumb_{secrets.token_hex(4)}.jpg" if thumb_url else None
    
    try:
        # Added extra Accept headers to trick strict servers
        dl_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }
        
        async with aiohttp.ClientSession(timeout=timeout, headers=dl_headers) as session:
            if thumb_url:
                async with session.get(thumb_url) as t_resp:
                    if t_resp.status == 200:
                        async with aiofiles.open(thumb_path, mode='wb') as f:
                            await f.write(await t_resp.read())
            
            async with session.get(video_url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: Download blocked by Terabox.")
                
                content_type = resp.headers.get('Content-Type', '')
                if 'text/html' in content_type:
                    raise Exception("Terabox returned a webpage instead of a file.")

                async with aiofiles.open(local_filename, mode='wb') as f:
                    while True:
                        chunk = await resp.content.read(2 * 1024 * 1024) 
                        if not chunk: break
                        await f.write(chunk)
                        
        # --- THE FIX: Physical File Verification ---
        if os.path.exists(local_filename):
            file_size_bytes = os.path.getsize(local_filename)
            # If the downloaded file is less than 100KB, it is a fake/error file
            if file_size_bytes < 100 * 1024:
                raise Exception("Terabox API provided an expired or restricted file link.")
                
    except Exception as e:
        await anim_msg.edit_text(f"<blockquote>❌ <b>Download Interrupted:</b>\n<code>{e}</code></blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        if os.path.exists(local_filename): os.remove(local_filename)
        return

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

        await anim_msg.edit_text("<blockquote><code>[✅] Complete</code></blockquote>")
        
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
        # Ensure the bot tracks this new "Download More" button prompt if we need to auto-delete it later
        active_welcome_msgs[message.chat.id] = sent_vid.id
        
        asyncio.create_task(delete_after(client, message.chat.id, sent_vid.id, FILE_DELETE_TIME))

    except Exception as e:
        await anim_msg.edit_text(f"<blockquote>❌ <b>Upload Error:</b>\n<code>{e}</code></blockquote>")
    finally:
        if os.path.exists(local_filename): os.remove(local_filename)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        await safe_delete(anim_msg)

if __name__ == "__main__":
    print("Starting Terabox Bot...")
    app.run()
    
