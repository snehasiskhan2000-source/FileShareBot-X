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

logging.basicConfig(level=logging.INFO)

app = Client(
    "terabox_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=TERABOX_BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML
)

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
    asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))

@app.on_callback_query(filters.regex("terabox_start"))
async def callback_download_more(client, callback_query):
    await callback_query.message.reply_text("<blockquote>✨ <b>Transmit a Terabox Link</b> 🙌\n<i>Ready for the next payload.</i></blockquote>")
    await callback_query.answer()

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def process_terabox_link(client, message):
    raw_text = message.text
    text = raw_text.lower()
    
    if "terabox" not in text and "1024tera" not in text:
        await safe_delete(message)
        err = await message.reply_text("<blockquote>⚠️ <b>Invalid protocol.</b>\nRequires a Terabox URL.</blockquote>")
        asyncio.create_task(delete_after(client, err.chat.id, err.id, TEMP_MSG_DELETE_TIME))
        return

    await safe_delete(message)
    
    url_match = re.search(r"https?://[^\s]+", raw_text)
    if not url_match:
        err = await message.reply_text("<blockquote>❌ <b>Extraction Failed.</b> No valid URL detected.</blockquote>")
        asyncio.create_task(delete_after(client, err.chat.id, err.id, TEMP_MSG_DELETE_TIME))
        return
        
    clean_url = url_match.group(0)
    
    # ================= CACHE CHECK (Instant Delivery) =================
    cursor.execute('SELECT message_id FROM terabox_cache WHERE terabox_url = ?', (clean_url,))
    cached_result = cursor.fetchone()
    
    if cached_result:
        msg_id = cached_result[0]
        link_id = secrets.token_urlsafe(8)
        cursor.execute('INSERT INTO shared_files (link_id, message_id) VALUES (?, ?)', (link_id, msg_id))
        conn.commit()
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download More", callback_data="terabox_start")]])
        await client.copy_message(chat_id=message.chat.id, from_chat_id=CHANNEL_ID, message_id=msg_id, caption="\u200B", reply_markup=keyboard)
        return 
    # ==================================================================

    await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
    
    # PREMIUM DOWNLOADING ANIMATION
    anim_msg = await message.reply_text("<blockquote><code>[📡] Pinging xAPIVERSE servers...</code></blockquote>")

    api_url = 'https://xapiverse.com/api/terabox-pro'
    headers = {'Content-Type': 'application/json', 'xAPIverse-Key': XAPI_KEY}
    payload = {"url": clean_url} 
    
    video_url, file_name = None, "terabox_video.mp4" 
    timeout = aiohttp.ClientTimeout(total=3600) 
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if data.get("status") == "success" and data.get("list"):
                    file_data = data["list"][0]
                    video_url = file_data.get("fast_download_link") or file_data.get("download_link")
                    file_name = file_data.get("name", "terabox_video.mp4")
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

    await anim_msg.edit_text("<blockquote><code>[🔓] Bypassing Terabox security...</code></blockquote>")
    await asyncio.sleep(0.5)
    await anim_msg.edit_text("<blockquote><code>[📥] Downloading to Render...</code>\n<code>[████░░░░░░] 40%</code></blockquote>")
    await client.send_chat_action(message.chat.id, enums.ChatAction.RECORD_VIDEO)
    
    os.makedirs("downloads", exist_ok=True)
    local_filename = f"downloads/{secrets.token_hex(4)}_{file_name}"
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(video_url) as resp:
                async with aiofiles.open(local_filename, mode='wb') as f:
                    while True:
                        chunk = await resp.content.read(2 * 1024 * 1024) 
                        if not chunk: break
                        await f.write(chunk)
    except Exception as e:
        await anim_msg.edit_text(f"<blockquote>❌ <b>Download Interrupted:</b>\n<code>{e}</code></blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        return

    await anim_msg.edit_text("<blockquote><code>[📤] Encrypting to secure vault...</code>\n<code>[████████░░] 80%</code></blockquote>")
    await client.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_VIDEO)

    link_id = secrets.token_urlsafe(8)
    fsb_link = f"https://t.me/{FILESHARE_BOT_USERNAME}?start={link_id}"
    caption = f"🔗 **Access Link:**\n<code>{fsb_link}</code>"

    try:
        saved_msg = await client.send_video(chat_id=CHANNEL_ID, video=local_filename, caption=caption, has_spoiler=True)
        
        cursor.execute('INSERT INTO shared_files (link_id, message_id) VALUES (?, ?)', (link_id, saved_msg.id))
        cursor.execute('INSERT INTO terabox_cache (terabox_url, message_id) VALUES (?, ?)', (clean_url, saved_msg.id))
        conn.commit()

        await anim_msg.edit_text("<blockquote><code>[✅] Transfer Complete</code>\n<code>[██████████] 100%</code></blockquote>")
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download More", callback_data="terabox_start")]])
        await client.copy_message(chat_id=message.chat.id, from_chat_id=CHANNEL_ID, message_id=saved_msg.id, caption="\u200B", reply_markup=keyboard)

    except Exception as e:
        await anim_msg.edit_text(f"<blockquote>❌ <b>Upload Error:</b>\n<code>{e}</code></blockquote>")
    finally:
        if os.path.exists(local_filename): os.remove(local_filename)
        await safe_delete(anim_msg)

if __name__ == "__main__":
    print("Starting Terabox Bot...")
    app.run()
    
