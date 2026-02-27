import os
import asyncio
import logging
import sqlite3
import secrets
from aiohttp import web
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================= Configuration =================
API_ID = int(os.getenv("API_ID", "1234567")) 
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH") 
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-100YOUR_CHANNEL_ID_HERE")) 
ADMIN_ID = int(os.getenv("ADMIN_ID", "YOUR_ADMIN_ID_HERE"))

AUTO_DELETE_TIME = 300 
TEMP_MSG_DELETE_TIME = 120 
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)

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

# ================= State Management =================
user_states = {}       
tracked_messages = {}  
media_group_cache = {} 

async def set_state(user_id: int, state: str): user_states[user_id] = state
async def get_state(user_id: int): return user_states.get(user_id)
async def clear_state(user_id: int): user_states.pop(user_id, None)

async def track_msg(user_id: int, msg_id: int):
    if user_id not in tracked_messages: tracked_messages[user_id] = []
    tracked_messages[user_id].append(msg_id)

async def wipe_tracked_msgs(client: Client, chat_id: int, user_id: int):
    msgs = tracked_messages.get(user_id, [])
    if msgs:
        try: await client.delete_messages(chat_id, msgs)
        except Exception: pass
    tracked_messages.pop(user_id, None)

# ================= Utility Functions =================
async def safe_delete(message):
    try: await message.delete()
    except Exception: pass

async def delete_after(client: Client, chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try: await client.delete_messages(chat_id, message_id)
    except Exception: pass

async def auto_delete_batch_task(client: Client, chat_id: int, message_ids: list):
    await asyncio.sleep(AUTO_DELETE_TIME)
    try: await client.delete_messages(chat_id, message_ids)
    except Exception as e: logging.error(f"Could not auto-delete: {e}")

# ================= Custom Filters =================
async def is_upload_state(_, __, message): return user_states.get(message.from_user.id) == "upload"
async def is_delete_state(_, __, message): return user_states.get(message.from_user.id) == "delete"

upload_filter = filters.create(is_upload_state)
delete_filter = filters.create(is_delete_state)

# ================= Commands =================
@app.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client, message):
    await safe_delete(message)
    user_id = message.from_user.id
    await wipe_tracked_msgs(client, message.chat.id, user_id)
    await clear_state(user_id)
    msg = await message.reply_text("<blockquote>🚫 <b>Action Cancelled</b>\nExited current mode safely.</blockquote>")
    asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    await safe_delete(message)
    args = message.command
    
    if len(args) > 1:
        link_id = args[1]
        cursor.execute('SELECT message_id FROM shared_files WHERE link_id = ?', (link_id,))
        results = cursor.fetchall()
        
        if results:
            await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
            # PREMIUM EXTRACT ANIMATION
            anim_msg = await message.reply_text("<blockquote><code>[🔍] Querying secure vault...</code></blockquote>")
            await asyncio.sleep(0.5)
            await anim_msg.edit_text("<blockquote><code>[🔐] Validating access token...</code></blockquote>")
            await asyncio.sleep(0.5)
            await anim_msg.edit_text("<blockquote><code>[📦] Decrypting file structure...</code></blockquote>")
            await asyncio.sleep(0.5)
            
            warning_text = f"<blockquote>⏳ <b>Delivering {len(results)} file(s)...</b>\n⚠️ <i>Destruction sequence initiates in {AUTO_DELETE_TIME // 60} minutes.</i></blockquote>"
            await anim_msg.edit_text(warning_text)
            sent_message_ids = [anim_msg.id] 
            
            await client.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_DOCUMENT)
            for row in results:
                msg_id = row[0]
                try:
                    sent_msg = await client.copy_message(chat_id=message.chat.id, from_chat_id=CHANNEL_ID, message_id=msg_id, caption="\u200B")
                    sent_message_ids.append(sent_msg.id)
                except Exception: pass
            
            if len(sent_message_ids) > 1:
                asyncio.create_task(auto_delete_batch_task(client, message.chat.id, sent_message_ids))
        else:
            err_msg = await message.reply_text("<blockquote>❌ <b>Access Denied</b>\nToken invalid, purged, or expired.</blockquote>")
            asyncio.create_task(delete_after(client, err_msg.chat.id, err_msg.id, TEMP_MSG_DELETE_TIME))
    else:
        welcome_msg = await message.reply_text(
            "<blockquote>✨ <b>Welcome to FileShareBot</b> ✨\n"
            "🛡 <i>The ultimate tool for secure file distribution.</i></blockquote>"
        )
        asyncio.create_task(delete_after(client, welcome_msg.chat.id, welcome_msg.id, TEMP_MSG_DELETE_TIME))

@app.on_message(filters.command("upload") & filters.private)
async def cmd_upload(client, message):
    await safe_delete(message)
    if message.from_user.id != ADMIN_ID: return 
    await set_state(message.from_user.id, "upload")
    msg = await message.reply_text("<blockquote>🚀 <b>Upload Uplink Established</b>\n📁 <i>Awaiting payload transfer...</i></blockquote>")
    await track_msg(message.from_user.id, msg.id)
    asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))

@app.on_message(filters.command("admin") & filters.private)
async def cmd_admin(client, message):
    await safe_delete(message)
    if message.from_user.id != ADMIN_ID: return 
    await clear_state(message.from_user.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Wipe Specific Link", callback_data="admin_clear_specific")],
        [InlineKeyboardButton("⚠️ Purge ALL Databases", callback_data="admin_clear_all")]
    ])
    await message.reply_text("<blockquote>⚙️ <b>Admin Root Access</b>\nSelect an override command:</blockquote>", reply_markup=keyboard)

# ================= Hidden Upload Logic =================
@app.on_message(upload_filter & filters.text & ~filters.command(["start", "upload", "cancel", "admin"]) & filters.private)
async def process_upload_text(client, message):
    if message.from_user.id != ADMIN_ID: return
    await safe_delete(message)
    err_msg = await message.reply_text("<blockquote>⚠️ <b>Invalid Payload Type!</b>\nStrictly media and documents accepted.\n💡 <i>Type /cancel to abort.</i></blockquote>")
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
    new_caption = f"{original_caption}\n\n<blockquote>🔗 <b>Secure Access Link:</b>\n<code>{share_link}</code></blockquote>".strip()

    if is_first:
        await client.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_DOCUMENT)
        # PREMIUM UPLOAD ANIMATION
        anim_msg = await message.reply_text("<blockquote><code>[⚡] Initializing secure uplink...</code>\n<code>[██░░░░░░░░] 20%</code></blockquote>")
        await track_msg(message.from_user.id, anim_msg.id)
        await asyncio.sleep(0.4)
        await anim_msg.edit_text("<blockquote><code>[🔐] Encrypting payload...</code>\n<code>[██████░░░░] 60%</code></blockquote>")
        await asyncio.sleep(0.4)
        await anim_msg.edit_text("<blockquote><code>[📦] Finalizing database entry...</code>\n<code>[██████████] 100%</code></blockquote>")

    try:
        if message.video: saved_msg = await client.send_video(chat_id=CHANNEL_ID, video=message.video.file_id, caption=new_caption, has_spoiler=True)
        elif message.photo: saved_msg = await client.send_photo(chat_id=CHANNEL_ID, photo=message.photo.file_id, caption=new_caption)
        elif message.document: saved_msg = await client.send_document(chat_id=CHANNEL_ID, document=message.document.file_id, caption=new_caption)
        else: saved_msg = await client.copy_message(chat_id=CHANNEL_ID, from_chat_id=message.chat.id, message_id=message.id, caption=new_caption)
            
        cursor.execute('INSERT INTO shared_files (link_id, message_id) VALUES (?, ?)', (link_id, saved_msg.id))
        conn.commit()
        
        if is_first and anim_msg:
            success_text = (
                "<blockquote>✅ <b>Payload Uploaded Successfully!</b>\n"
                "📦 <i>Secured under a single encrypted link.</i></blockquote>\n"
                "🔗 <b>Shareable Link:</b>\n"
                f"<code>{share_link}</code>\n\n"
                "💡 <i>Transmit more files or /cancel to abort.</i>"
            )
            await anim_msg.edit_text(success_text)
            asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))
            
    except Exception as e:
        if is_first and anim_msg:
            await anim_msg.edit_text(f"<blockquote>❌ <b>Upload Error:</b>\n<code>{e}</code></blockquote>")
            asyncio.create_task(delete_after(client, anim_msg.chat.id, anim_msg.id, TEMP_MSG_DELETE_TIME))

# ================= Admin Panel Logic =================
@app.on_callback_query(filters.regex("admin_clear_all"))
async def process_clear_all(client, callback_query):
    if callback_query.from_user.id != ADMIN_ID: return
    cursor.execute('DELETE FROM shared_files')
    conn.commit()
    await callback_query.message.edit_text("<blockquote>✅ <b>Database Purged.</b>\nAll existing access links are now dead.</blockquote>")

@app.on_callback_query(filters.regex("admin_clear_specific"))
async def process_clear_specific(client, callback_query):
    if callback_query.from_user.id != ADMIN_ID: return
    await set_state(callback_query.from_user.id, "delete")
    await callback_query.message.reply_text("<blockquote>🔗 <b>Target Acquisition</b>\nProvide the secure link to execute deletion:</blockquote>")
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
                try: await client.delete_messages(CHANNEL_ID, row[0])
                except Exception: pass 
            
            cursor.execute('DELETE FROM shared_files WHERE link_id = ?', (link_id,))
            conn.commit()
            msg = await message.reply_text(f"<blockquote>✅ <b>Deletion Executed</b>\n{len(results)} file(s) permanently erased.</blockquote>")
            asyncio.create_task(delete_after(client, msg.chat.id, msg.id, TEMP_MSG_DELETE_TIME))
        else:
            err = await message.reply_text("<blockquote>❌ <b>Target Not Found</b>\nLink does not exist in the registry.</blockquote>")
            asyncio.create_task(delete_after(client, err.chat.id, err.id, TEMP_MSG_DELETE_TIME))
            
    except Exception as e:
        await message.reply_text(f"<blockquote>❌ <b>Error:</b> <code>{e}</code></blockquote>")
    finally:
        await clear_state(message.from_user.id)

# ================= Render Keep-Alive Server =================
async def handle_ping(request): return web.Response(text="Bot is running smoothly on Pyrogram!")
async def web_server():
    server = web.Application()
    server.router.add_get('/', handle_ping)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

async def main():
    print("Starting Web Server...")
    asyncio.create_task(web_server())
    print("Starting Pyrogram Bot...")
    await app.start()
    from pyrogram import idle
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
        
