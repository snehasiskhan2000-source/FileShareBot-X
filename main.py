import os
import asyncio
import logging
import sqlite3
import secrets
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties

# ================= Configuration =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-100YOUR_CHANNEL_ID_HERE")) 
ADMIN_ID = int(os.getenv("ADMIN_ID", "YOUR_ADMIN_ID_HERE"))
AUTO_DELETE_TIME = 300 # 5 minutes for files
TEMP_MSG_DELETE_TIME = 120 # 2 minutes for temporary bot messages
PORT = int(os.getenv("PORT", 8080)) 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()
router = Router()

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

# ================= Globals & States =================
class BotStates(StatesGroup):
    waiting_for_upload = State()
    waiting_for_delete_link = State()

media_group_cache = {}

# ================= Utility Functions =================
async def safe_delete(message: Message):
    """Instantly deletes a message and ignores errors if it fails."""
    try:
        await message.delete()
    except Exception:
        pass

async def delete_after(chat_id: int, message_id: int, delay: int):
    """Waits for a specific delay, then deletes a single message."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def auto_delete_batch_task(chat_id: int, message_ids: list):
    """Waits for 5 minutes, then deletes a batch of messages (used for files)."""
    await asyncio.sleep(AUTO_DELETE_TIME)
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logging.error(f"Could not auto-delete message {msg_id}: {e}")

async def track_msg(state: FSMContext, msg_id: int):
    """Tracks temporary bot messages in state so they can be instantly wiped on /cancel."""
    data = await state.get_data()
    temp_msgs = data.get("temp_msgs", [])
    temp_msgs.append(msg_id)
    await state.update_data(temp_msgs=temp_msgs)

# ================= Global Command Catchers =================
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    # 1. Instantly delete the /cancel command
    await safe_delete(message)
    
    # 2. Instantly wipe any tracked temporary bot messages (upload/error messages)
    data = await state.get_data()
    temp_msgs = data.get("temp_msgs", [])
    for msg_id in temp_msgs:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
        except Exception:
            pass
            
    await state.clear()
    
    # 3. Send confirmation and auto-delete it after 2 minutes
    msg = await message.answer("🚫 <b>Action cancelled. Exited current mode.</b>")
    asyncio.create_task(delete_after(msg.chat.id, msg.message_id, TEMP_MSG_DELETE_TIME))


# ================= User Commands =================
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject = None):
    # Instantly delete /start
    await safe_delete(message)
        
    args = message.text.split() if message.text else []
    
    if len(args) > 1:
        link_id = args[1]
        cursor.execute('SELECT message_id FROM shared_files WHERE link_id = ?', (link_id,))
        results = cursor.fetchall()
        
        if results:
            # Add the warning message to the sent_message_ids so it gets deleted WITH the files
            warning_msg = await message.answer(f"⏳ <i>Sending {len(results)} file(s)... This will self-destruct in {AUTO_DELETE_TIME // 60} minutes.</i>")
            sent_message_ids = [warning_msg.message_id] 
            
            for row in results:
                msg_id = row[0]
                try:
                    sent_msg = await bot.copy_message(
                        chat_id=message.from_user.id,
                        from_chat_id=CHANNEL_ID,
                        message_id=msg_id,
                        caption="\u200B" 
                    )
                    sent_message_ids.append(sent_msg.message_id)
                except Exception as e:
                    logging.error(f"Failed to copy file {msg_id}: {e}")
            
            if len(sent_message_ids) > 1:
                asyncio.create_task(auto_delete_batch_task(message.from_user.id, sent_message_ids))
        else:
            err_msg = await message.answer("❌ <b>Invalid or expired link.</b>")
            asyncio.create_task(delete_after(err_msg.chat.id, err_msg.message_id, TEMP_MSG_DELETE_TIME))
    else:
        welcome_msg = await message.answer(
            "✨ <b>Welcome to FileShareBot</b> ✨\n\n"
            "🛡 <i>I can securely deliver files to you.</i>"
        )
        # Auto-delete Welcome message after 2 mins
        asyncio.create_task(delete_after(welcome_msg.chat.id, welcome_msg.message_id, TEMP_MSG_DELETE_TIME))

# ================= Hidden Upload Logic =================
@router.message(Command("upload"))
async def cmd_upload(message: Message, state: FSMContext):
    # Instantly delete /upload
    await safe_delete(message)
    
    if message.from_user.id != ADMIN_ID:
        return 
    
    await state.set_state(BotStates.waiting_for_upload)
    msg = await message.answer(
        "🚀 <b>Upload Mode Activated</b> 🚀\n\n"
        "📁 <i>Send me any file (or multiple files at once) to securely store them.</i>"
    )
    # Track to wipe if user types /cancel
    await track_msg(state, msg.message_id)
    # Also set fallback 2-minute timer
    asyncio.create_task(delete_after(msg.chat.id, msg.message_id, TEMP_MSG_DELETE_TIME))

@router.message(BotStates.waiting_for_upload, F.text)
async def process_upload_text(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    # Instantly delete their invalid text message
    await safe_delete(message)
    
    err_msg = await message.answer(
        "⚠️ <b>Text not allowed!</b>\n"
        "Please send a FILE (Photo, Video, Document).\n"
        "💡 Type /cancel to exit."
    )
    await track_msg(state, err_msg.message_id)
    asyncio.create_task(delete_after(err_msg.chat.id, err_msg.message_id, TEMP_MSG_DELETE_TIME))

@router.message(BotStates.waiting_for_upload, F.content_type.in_({'photo', 'video', 'document', 'audio', 'voice'}))
async def process_upload_media(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return

    is_media_group = message.media_group_id is not None
    if is_media_group:
        if message.media_group_id in media_group_cache:
            link_id = media_group_cache[message.media_group_id]
            is_first = False
        else:
            link_id = secrets.token_urlsafe(8)
            media_group_cache[message.media_group_id] = link_id
            is_first = True
    else:
        link_id = secrets.token_urlsafe(8)
        is_first = True

    bot_info = await bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start={link_id}"
    
    original_caption = message.html_text or ""
    new_caption = f"{original_caption}\n\n🔗 <b>Access Link:</b>\n<code>{share_link}</code>".strip()

    try:
        if message.video:
            saved_msg = await bot.send_video(
                chat_id=CHANNEL_ID,
                video=message.video.file_id,
                caption=new_caption,
                parse_mode="HTML",
                has_spoiler=True
            )
        elif message.photo:
            saved_msg = await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=message.photo[-1].file_id,
                caption=new_caption,
                parse_mode="HTML"
            )
        elif message.document:
            saved_msg = await bot.send_document(
                chat_id=CHANNEL_ID,
                document=message.document.file_id,
                caption=new_caption,
                parse_mode="HTML"
            )
        else:
            saved_msg = await bot.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=message.from_user.id,
                message_id=message.message_id,
                caption=new_caption,
                parse_mode="HTML"
            )
            
        cursor.execute('INSERT INTO shared_files (link_id, message_id) VALUES (?, ?)', (link_id, saved_msg.message_id))
        conn.commit()
        
        if is_first:
            success_msg = await message.answer(
                "✅ <b>File(s) Uploaded Successfully!</b> 🎉\n"
                "📦 <i>(Multiple files sent together are grouped under this single link)</i>\n\n"
                "🔗 <b>Shareable Link:</b>\n"
                f"<code>{share_link}</code>\n\n"
                "💡 <i>Send more files or type /cancel to exit.</i>"
            )
            await track_msg(state, success_msg.message_id)
            asyncio.create_task(delete_after(success_msg.chat.id, success_msg.message_id, TEMP_MSG_DELETE_TIME))
            
    except Exception as e:
        if is_first:
            err_msg = await message.answer(f"❌ <b>Error storing file:</b> {e}")
            await track_msg(state, err_msg.message_id)
            asyncio.create_task(delete_after(err_msg.chat.id, err_msg.message_id, TEMP_MSG_DELETE_TIME))

# ================= Admin Panel Logic =================
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    # Instantly delete /admin
    await safe_delete(message)
    
    if message.from_user.id != ADMIN_ID: return 
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Clear Specific Link", callback_data="admin_clear_specific")],
        [InlineKeyboardButton(text="⚠️ Clear ALL Database", callback_data="admin_clear_all")]
    ])
    
    await message.answer("⚙️ <b>Admin Control Panel</b>\nSelect an action below:", reply_markup=keyboard)

@router.callback_query(F.data == "admin_clear_all")
async def process_clear_all(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    
    cursor.execute('DELETE FROM shared_files')
    conn.commit()
    
    await callback.message.edit_text("✅ <b>Database Cleared.</b>\nAll existing links have been invalidated.")
    await callback.answer("Database wiped.")

@router.callback_query(F.data == "admin_clear_specific")
async def process_clear_specific(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    
    await state.set_state(BotStates.waiting_for_delete_link)
    await callback.message.answer("🔗 <b>Send me the special link</b> of the file(s) you want to delete:")
    await callback.answer()

@router.message(BotStates.waiting_for_delete_link)
async def process_delete_link(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return

    # User's invalid text/link gets safely deleted so chat stays clean
    await safe_delete(message)

    try:
        link_id = message.text.split("?start=")[-1]
        
        cursor.execute('SELECT message_id FROM shared_files WHERE link_id = ?', (link_id,))
        results = cursor.fetchall()
        
        if results:
            for row in results:
                msg_id = row[0]
                try:
                    await bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
                except Exception:
                    pass 
            
            cursor.execute('DELETE FROM shared_files WHERE link_id = ?', (link_id,))
            conn.commit()
            
            await message.answer(f"✅ <b>{len(results)} File(s) permanently deleted</b> from the channel and database.")
        else:
            err = await message.answer("❌ <b>Link not found in database.</b>")
            asyncio.create_task(delete_after(err.chat.id, err.message_id, TEMP_MSG_DELETE_TIME))
            
    except Exception as e:
        await message.answer(f"❌ <b>Error:</b> {e}")
    finally:
        await state.clear()

# ================= Render Keep-Alive Server =================
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# ================= Main Execution =================
async def main():
    dp.include_router(router)
    asyncio.create_task(web_server())
    print("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
