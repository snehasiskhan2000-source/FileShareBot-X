import os
import asyncio
import logging
import sqlite3
import secrets
from aiohttp import web
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================= Configuration =================
# Note: Pyrogram requires API_ID and API_HASH from my.telegram.org
API_ID = int(os.getenv("API_ID", "1234567")) # Replace with your API ID
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH") # Replace with your API Hash
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-100YOUR_CHANNEL_ID_HERE")) 
ADMIN_ID = int(os.getenv("ADMIN_ID", "YOUR_ADMIN_ID_HERE"))

AUTO_DELETE_TIME = 300 # 5 minutes for files
TEMP_MSG_DELETE_TIME = 120 # 2 minutes for temporary bot messages
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)

# Initialize Pyrogram Client
app = Client(
    "file_share_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML
)

# ================= Database Setup =================
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS shared_files (
        link_id TEXT,
        message_id INTEGER
    )
''')
conn.commit()

# ================= State Management (Since Pyrogram lacks FSM) =================
user_states = {}       # Maps user_id -> current state (e.g., "upload", "delete")
tracked_messages = {}  # Maps user_id -> [list of temporary message IDs to delete on /cancel]
media_group_cache = {} # Maps media_group_id -> secret link

async def set_state(user_id: int, state: str):
    user_states[user_id] = state

async def get_state(user_id: int):
    return user_states.get(user_id)

async def clear_state(user_id: int):
    user_states.pop(user_id, None)

async def track_msg(user_id: int, msg_id: int):
    if user_id not in tracked_messages:
        tracked_messages[user_id] = []
    tracked_messages[user_id].append(msg_id)

async def wipe_tracked_msgs(client: Client, chat_id: int, user_id: int):
    msgs = tracked_messages.get(user_id, [])
    if msgs:
        try:
            await client.delete_messages(chat_id, msgs)
        except Exception:
            pass
    tracked_messages.pop(user_id, None)

# ================= Utility Functions =================
async def safe_delete(message):
    try:
        await message.delete()
    except Exception:
        pass

async def delete_after(client: Client, chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, message_id)
    except Exception:
        pass

async def auto_delete_batch_task(client: Client, chat_id: int, message_ids: list):
    await asyncio.sleep(AUTO_DELETE_TIME)
    try:
        await client.delete_messages(chat_id, message_ids)
    except Exception as e:
        logging.error(f"Could not auto-delete messages: {e}")

# ================= Custom Filters =================
async def is_upload_state(_, __, message):
    return user_states.get(message.from_user.id) == "upload"

async def is_delete_state(_, __, message):
    return user_states.get(message.from_user.id) == "delete"

upload_filter = filters.create(is_upload_state)
delete_filter = filters.create(is_delete_state)

# ================= Global Command Catchers =================
@app.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client, message):
    await safe_delete(message)
    user_id = message.from_user.id
    
    await wipe_tracked_msgs(client, message.chat.id, user_id)
    await clear_state(user_id)
    
    msg = await message.reply_text("<blockquote>🚫 <b>Action Cancelled</b>\nExited current mode safely.</blockquote>")
    asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))

# ================= User Commands (Download Logic) =================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    await safe_delete(message)
    args = message.command
    
    if len(args) > 1:
        link_id = args[1]
        cursor.execute('SELECT message_id FROM shared_files WHERE link_id = ?', (link_id,))
        results = cursor.fetchall()
        
        if results:
            # --- EXTRACTION ANIMATION ---
            await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
            anim_msg = await message.reply_text("<blockquote>🔍 <b>Locating secure files...</b></blockquote>")
            await asyncio.sleep(0.4)
            await anim_msg.edit_text("<blockquote>🔓 <b>Decrypting access...</b></blockquote>")
            await asyncio.sleep(0.4)
            
            warning_text = f"<blockquote>⏳ <b>Sending {len(results)} file(s)...</b>\n⚠️ <i>This batch will self-destruct in {AUTO_DELETE_TIME // 60} minutes.</i></blockquote>"
            await anim_msg.edit_text(warning_text)
            sent_message_ids = [anim_msg.id] 
            
            await client.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_DOCUMENT)
            for row in results:
                msg_id = row[0]
                try:
                    sent_msg = await client.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=CHANNEL_ID,
                        message_id=msg_id,
                        caption="\u200B" # Zero-width space trick for Pyrogram
                    )
                    sent_message_ids.append(sent_msg.id)
                except Exception as e:
                    logging.error(f"Error copying: {e}")
            
            if len(sent_message_ids) > 1:
                asyncio.create_task(auto_delete_batch_task(client, message.chat.id, sent_message_ids))
        else:
            err_msg = await message.reply_text("<blockquote>❌ <b>Access Denied</b>\nInvalid or expired link.</blockquote>")
            asyncio.create_task(delete_after(client, err_msg.chat.id, err_msg.id, TEMP_MSG_DELETE_TIME))
    else:
        welcome_msg = await message.reply_text(
            "<blockquote>✨ <b>Welcome to FileShareBot</b> ✨\n"
            "🛡 <i>The ultimate tool for secure file distribution.</i></blockquote>"
        )
        asyncio.create_task(delete_after(client, welcome_msg.chat.id, welcome_msg.id, TEMP_MSG_DELETE_TIME))

# ================= Hidden Upload Logic =================
@app.on_message(filters.command("upload") & filters.private)
async def cmd_upload(client, message):
    await safe_delete(message)
    if message.from_user.id != ADMIN_ID: return 
    
    await set_state(message.from_user.id, "upload")
    msg = await message.reply_text(
        "<blockquote>🚀 <b>Upload Mode Activated</b>\n"
        "📁 <i>Send me any file (or batch) to securely store them.</i></blockquote>"
    )
    await track_msg(message.from_user.id, msg.id)
    asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))

@app.on_message(upload_filter & filters.text & filters.private)
async def process_upload_text(client, message):
    if message.from_user.id != ADMIN_ID: return
    await safe_delete(message)
    
    if message.text.startswith('/'): return # Let command handlers deal with it
    
    err_msg = await message.reply_text(
        "<blockquote>⚠️ <b>Text not allowed!</b>\n"
        "Please send a FILE (Photo, Video, Document).\n"
        "💡 <i>Type /cancel to exit.</i></blockquote>"
    )
    await track_msg(message.from_user.id, err_msg.id)
    asyncio.create_task(delete_after(client, err_msg.chat.id, err_msg.id, TEMP_MSG_DELETE_TIME))

@app.on_message(upload_filter & filters.media & filters.private)
async def process_upload_media(client, message):
    if message.from_user.id != ADMIN_ID: return

    is_media_group = message.media_group_id is not None
    if is_media_group:
        if message.media_group_id in media_group_cache:
            link_id = media_group_cache[message.media_group_id]
            is_first = False
            anim_msg = None
        else:
            link_id = secrets.token_urlsafe(8)
            media_group_cache[message.media_group_id] = link_id
            is_first = True
    else:
        link_id = secrets.token_urlsafe(8)
        is_first = True

    bot_info = await client.get_me()
    share_link = f"https://t.me/{bot_info.username}?start={link_id}"
    
    original_caption = message.caption and message.caption.html or ""
    new_caption = f"{original_caption}\n\n<blockquote>🔗 <b>Access Link:</b>\n<code>{share_link}</code></blockquote>".strip()

    # --- UPLOAD PROGRESS ANIMATION ---
    if is_first:
        await client.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_DOCUMENT)
        anim_msg = await message.reply_text("<blockquote>🔄 <b>Processing payload...</b>\n[■□□□□] 20%</blockquote>")
        await track_msg(message.from_user.id, anim_msg.id)
        await asyncio.sleep(0.3)
        await anim_msg.edit_text("<blockquote>⏳ <b>Encrypting data...</b>\n[■■■□□] 60%</blockquote>")
        await asyncio.sleep(0.3)
        await anim_msg.edit_text("<blockquote>🔐 <b>Generating link...</b>\n[■■■■■] 100%</blockquote>")

    try:
        # Save based on type to respect spoiler tag requests
        if message.video:
            saved_msg = await client.send_video(chat_id=CHANNEL_ID, video=message.video.file_id, caption=new_caption, has_spoiler=True)
        elif message.photo:
            saved_msg = await client.send_photo(chat_id=CHANNEL_ID, photo=message.photo.file_id, caption=new_caption)
        elif message.document:
            saved_msg = await client.send_document(chat_id=CHANNEL_ID, document=message.document.file_id, caption=new_caption)
        else:
            saved_msg = await client.copy_message(chat_id=CHANNEL_ID, from_chat_id=message.chat.id, message_id=message.id, caption=new_caption)
            
        cursor.execute('INSERT INTO shared_files (link_id, message_id) VALUES (?, ?)', (link_id, saved_msg.id))
        conn.commit()
        
        if is_first and anim_msg:
            success_text = (
                "<blockquote>✅ <b>File(s) Uploaded Successfully!</b>\n"
                "📦 <i>Grouped under a single secure link.</i></blockquote>\n"
                "🔗 <b>Shareable Link:</b>\n"
                f"<code>{share_link}</code>\n\n"
                "💡 <i>Send more files or type /cancel to exit.</i>"
            )
            await anim_msg.edit_text(success_text)
            asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
            
    except Exception as e:
        if is_first and anim_msg:
            await anim_msg.edit_text(f"<blockquote>❌ <b>Error storing file:</b>\n{e}</blockquote>")
            asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))

# ================= Admin Panel Logic =================
@app.on_message(filters.command("admin") & filters.private)
async def cmd_admin(client, message):
    await safe_delete(message)
    if message.from_user.id != ADMIN_ID: return 
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Clear Specific Link", callback_data="admin_clear_specific")],
        [InlineKeyboardButton("⚠️ Clear ALL Database", callback_data="admin_clear_all")]
    ])
    
    await message.reply_text("<blockquote>⚙️ <b>Admin Control Panel</b>\nSelect an action below:</blockquote>", reply_markup=keyboard)

@app.on_callback_query(filters.regex("admin_clear_all"))
async def process_clear_all(client, callback_query):
    if callback_query.from_user.id != ADMIN_ID: return
    cursor.execute('DELETE FROM shared_files')
    conn.commit()
    await callback_query.message.edit_text("<blockquote>✅ <b>Database Cleared.</b>\nAll existing links are now dead.</blockquote>")

@app.on_callback_query(filters.regex("admin_clear_specific"))
async def process_clear_specific(client, callback_query):
    if callback_query.from_user.id != ADMIN_ID: return
    await set_state(callback_query.from_user.id, "delete")
    await callback_query.message.reply_text("<blockquote>🔗 <b>Target Acquisition</b>\nSend me the special link of the file(s) to delete:</blockquote>")
    await callback_query.answer()

@app.on_message(delete_filter & filters.text & filters.private)
async def process_delete_link(client, message):
    if message.from_user.id != ADMIN_ID: return
    await safe_delete(message)

    if message.text.startswith('/'): return

    try:
        link_id = message.text.split("?start=")[-1]
        cursor.execute('SELECT message_id FROM shared_files WHERE link_id = ?', (link_id,))
        results = cursor.fetchall()
        
        if results:
            for row in results:
                msg_id = row[0]
                try:
                    await client.delete_messages(CHANNEL_ID, msg_id)
                except Exception:
                    pass 
            
            cursor.execute('DELETE FROM shared_files WHERE link_id = ?', (link_id,))
            conn.commit()
            
            msg = await message.reply_text(f"<blockquote>✅ <b>Purge Complete</b>\n{len(results)} file(s) permanently erased.</blockquote>")
            asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))
        else:
            err = await message.reply_text("<blockquote>❌ <b>Link not found in database.</b></blockquote>")
            asyncio.create_task(delete_after(client, err.chat.id, err.id, TEMP_MSG_DELETE_TIME))
            
    except Exception as e:
        await message.reply_text(f"<blockquote>❌ <b>Error:</b> {e}</blockquote>")
    finally:
        await clear_state(message.from_user.id)

# ================= Render Keep-Alive Server =================
async def handle_ping(request):
    return web.Response(text="Bot is running smoothly on Pyrogram!")

async def web_server():
    server = web.Application()
    server.router.add_get('/', handle_ping)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# ================= Main Execution =================
async def main():
    print("Starting Web Server...")
    asyncio.create_task(web_server())
    
    print("Starting Pyrogram Bot...")
    await app.start()
    
    # Keeps the bot running
    from pyrogram import idle
    await idle()
    
    await app.stop()

if __name__ == "__main__":
    app.run(main())
            
