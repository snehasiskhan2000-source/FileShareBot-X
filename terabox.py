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
FILESHARE_BOT_USERNAME = os.getenv("FILESHARE_BOT_USERNAME", "FSB69_BOT") # Without the @

TEMP_MSG_DELETE_TIME = 120 # 2 mins auto-delete

logging.basicConfig(level=logging.INFO)

app = Client(
    "terabox_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=TERABOX_BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML
)

# ================= Database Setup =================
# Connects to the EXACT SAME database your FileShareBot uses
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS shared_files (
        link_id TEXT,
        message_id INTEGER
    )
''')
conn.commit()

# ================= Utility Functions =================
async def safe_delete(message):
    """Instantly deletes a message and ignores errors if it fails."""
    try:
        await message.delete()
    except Exception:
        pass

async def delete_after(client, chat_id, message_id, delay):
    """Waits for a specific delay, then deletes a single message."""
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, message_id)
    except Exception:
        pass

# ================= Bot Logic =================

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    await safe_delete(message)
    msg = await message.reply_text("<blockquote>✨ **Send A Terabox Video Link 🙌**</blockquote>")
    asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))

@app.on_callback_query(filters.regex("terabox_start"))
async def callback_download_more(client, callback_query):
    # This triggers when they click "Download More"
    await callback_query.message.reply_text("<blockquote>✨ **Send A Terabox Video Link 🙌**</blockquote>")
    await callback_query.answer()

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def process_terabox_link(client, message):
    raw_text = message.text
    text = raw_text.lower()
    
    # 1. Reject non-Terabox text
    if "terabox" not in text and "1024tera" not in text:
        await safe_delete(message)
        err = await message.reply_text("<blockquote>⚠️ **Please send a valid Terabox link.**</blockquote>")
        asyncio.create_task(delete_after(client, err.chat.id, err.id, TEMP_MSG_DELETE_TIME))
        return

    await safe_delete(message)
    
    # 2. Surgically extract ONLY the URL from the forwarded text block
    url_match = re.search(r"https?://[^\s]+", raw_text)
    if not url_match:
        err = await message.reply_text("<blockquote>❌ **No valid URL found in the message.**</blockquote>")
        asyncio.create_task(delete_after(client, err.chat.id, err.id, TEMP_MSG_DELETE_TIME))
        return
        
    clean_url = url_match.group(0)
    
    # --- ANIMATION STEP 1: VALIDATING ---
    await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
    anim_msg = await message.reply_text("<blockquote>🔍 **Validating Terabox link...**\n[■□□□□] 20%</blockquote>")

    # 3. Fetch Direct Link via xAPIVERSE
    api_url = 'https://xapiverse.com/api/terabox-pro'
    headers = {
        'Content-Type': 'application/json',
        'xAPIverse-Key': XAPI_KEY
    }
    payload = {"url": clean_url} # Send ONLY the clean URL
    
    video_url = None
    file_name = "terabox_video.mp4" 
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers) as resp:
                data = await resp.json()
                
                # Parse the specific JSON array structure
                if data.get("status") == "success" and data.get("list"):
                    file_data = data["list"][0]
                    video_url = file_data.get("fast_download_link") or file_data.get("download_link")
                    file_name = file_data.get("name", "terabox_video.mp4")
                else:
                    api_error_msg = data.get("message", "API returned an error or empty list.")
                    raise Exception(api_error_msg)
                    
    except Exception as e:
        await anim_msg.edit_text(f"<blockquote>❌ **API Extraction Error:**\n{e}</blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        return

    if not video_url:
        await anim_msg.edit_text("<blockquote>❌ **Failed to extract video.** The link might be dead or private.</blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        return

    # --- ANIMATION STEP 2: DOWNLOADING ---
    await anim_msg.edit_text("<blockquote>⬇️ **Downloading to Render memory...**\n[■■■□□] 60%</blockquote>")
    await client.send_chat_action(message.chat.id, enums.ChatAction.RECORD_VIDEO)
    
    os.makedirs("downloads", exist_ok=True)
    local_filename = f"downloads/{secrets.token_hex(4)}_{file_name}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(video_url) as resp:
                async with aiofiles.open(local_filename, mode='wb') as f:
                    while True:
                        chunk = await resp.content.read(1024 * 1024) 
                        if not chunk: break
                        await f.write(chunk)
    except Exception as e:
        await anim_msg.edit_text(f"<blockquote>❌ **Download failed:**\n{e}</blockquote>")
        asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
        return

    # --- ANIMATION STEP 3: UPLOADING TO SECURE VAULT ---
    await anim_msg.edit_text("<blockquote>📤 **Uploading to secure vault...**\n[■■■■□] 80%</blockquote>")
    await client.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_VIDEO)

    link_id = secrets.token_urlsafe(8)
    fsb_link = f"https://t.me/{FILESHARE_BOT_USERNAME}?start={link_id}"
    caption = f"🔗 **Access Link:**\n<code>{fsb_link}</code>"

    try:
        saved_msg = await client.send_video(
            chat_id=CHANNEL_ID,
            video=local_filename,
            caption=caption,
            has_spoiler=True
        )
        
        # Share link with the FileShareBot's database
        cursor.execute('INSERT INTO shared_files (link_id, message_id) VALUES (?, ?)', (link_id, saved_msg.id))
        conn.commit()

        # --- ANIMATION STEP 4: FINALIZING ---
        await anim_msg.edit_text("<blockquote>✅ **Finalizing Delivery...**\n[■■■■■] 100%</blockquote>")
        
        # Add the Download More button
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download More", callback_data="terabox_start")]])
        
        await client.copy_message(
            chat_id=message.chat.id,
            from_chat_id=CHANNEL_ID,
            message_id=saved_msg.id,
            caption="\u200B", # Zero-width space for clean UI
            reply_markup=keyboard
        )

    except Exception as e:
        await anim_msg.edit_text(f"<blockquote>❌ **Upload error:**\n{e}</blockquote>")
    finally:
        # ALWAYS DELETE THE FILE FROM RENDER MEMORY TO SAVE SPACE
        if os.path.exists(local_filename):
            os.remove(local_filename)
        
        # Clean up the animation message
        await safe_delete(anim_msg)

if __name__ == "__main__":
    print("Starting Terabox Bot...")
    app.run()
    
