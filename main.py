import os
import asyncio
import logging
import sqlite3
import base64
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties

# ================= Configuration =================
# Replace these with your actual details, or use Environment Variables in Render
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-100YOUR_CHANNEL_ID_HERE")) 
ADMIN_ID = int(os.getenv("ADMIN_ID", "YOUR_ADMIN_ID_HERE"))
AUTO_DELETE_TIME = 300 # Time in seconds (e.g., 300 = 5 minutes)
PORT = int(os.getenv("PORT", 8080)) # Render assigns a PORT dynamically

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()
router = Router()

# ================= Database Setup =================
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS files (
        link_id TEXT PRIMARY KEY,
        message_id INTEGER
    )
''')
conn.commit()

# ================= FSM States =================
class BotStates(StatesGroup):
    waiting_for_upload = State()
    waiting_for_delete_link = State()

# ================= Utility Functions =================
def generate_link_id(message_id: int) -> str:
    """Encodes the message ID into a clean base64 string."""
    raw_bytes = f"file_{message_id}".encode('utf-8')
    return base64.urlsafe_b64encode(raw_bytes).decode('utf-8').rstrip("=")

async def auto_delete_task(chat_id: int, message_id: int):
    """Waits for the specified time, then deletes the message."""
    await asyncio.sleep(AUTO_DELETE_TIME)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logging.error(f"Could not auto-delete message: {e}")

# ================= User Commands =================
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject = None):
    # Instantly delete the user's /start message to keep it hidden
    try:
        await message.delete()
    except Exception:
        pass # Ignore if it fails for any reason
        
    args = message.text.split() if message.text else []
    
    # If the user clicked a special link (e.g., /start YmF0Y2g...)
    if len(args) > 1:
        link_id = args[1]
        cursor.execute('SELECT message_id FROM files WHERE link_id = ?', (link_id,))
        result = cursor.fetchone()
        
        if result:
            msg_id = result[0]
            try:
                # Copy file from private channel to user
                sent_msg = await bot.copy_message(
                    chat_id=message.from_user.id,
                    from_chat_id=CHANNEL_ID,
                    message_id=msg_id
                )
                
                await message.answer(f"⏳ <i>This file will self-destruct in {AUTO_DELETE_TIME // 60} minutes.</i>")
                
                # Start the auto-delete timer
                asyncio.create_task(auto_delete_task(message.from_user.id, sent_msg.message_id))
            except Exception as e:
                await message.answer("❌ <b>Error:</b> Could not retrieve the file. It may have been deleted by an admin.")
        else:
            await message.answer("❌ <b>Invalid or expired link.</b>")
    else:
        # Standard Start Command
        await message.answer("<b>Welcome to FileShareBot 🙌</b>\n\nI can securely deliver files to you.")

# ================= Hidden Upload Logic =================
@router.message(Command("upload"))
async def cmd_upload(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return # Ignore if not admin
    
    await state.set_state(BotStates.waiting_for_upload)
    await message.answer("📤 <b>Upload Mode Activated</b>\nSend me any file (Photo, Video, PDF, etc.) to store it in the database.")

@router.message(BotStates.waiting_for_upload)
async def process_upload(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    # 1. Intercept commands so they are NOT saved as files
    if message.text and message.text.startswith('/'):
        if message.text.lower() == '/cancel':
            await state.clear()
            await message.answer("🚫 <b>Action cancelled. Exited upload mode.</b>")
        else:
            await message.answer("⚠️ <b>Upload Mode is Active.</b>\nPlease send a file, or type /cancel to exit.")
        return

    # 2. Forward the file to the private channel
    try:
        copied_msg = await bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=message.from_user.id,
            message_id=message.message_id
        )
        
        # Generate link and save to DB
        link_id = generate_link_id(copied_msg.message_id)
        cursor.execute('INSERT INTO files (link_id, message_id) VALUES (?, ?)', (link_id, copied_msg.message_id))
        conn.commit()
        
        bot_info = await bot.get_me()
        share_link = f"https://t.me/{bot_info.username}?start={link_id}"
        
        await message.answer(
            f"✅ <b>File Uploaded Successfully!</b>\n\n"
            f"🔗 <b>Shareable Link:</b>\n<code>{share_link}</code>\n\n"
            f"<i>Send another file or type /cancel to exit upload mode.</i>"
        )
    except Exception as e:
        await message.answer(f"❌ <b>Error storing file:</b> {e}")

# ================= Admin Panel Logic =================
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return # Ignore if not admin
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Clear Specific File", callback_data="admin_clear_specific")],
        [InlineKeyboardButton(text="⚠️ Clear ALL Database", callback_data="admin_clear_all")]
    ])
    
    await message.answer("⚙️ <b>Admin Control Panel</b>\nSelect an action below:", reply_markup=keyboard)

@router.callback_query(F.data == "admin_clear_all")
async def process_clear_all(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    # Clears the SQLite database mapping (links will stop working)
    cursor.execute('DELETE FROM files')
    conn.commit()
    
    await callback.message.edit_text("✅ <b>Database Cleared.</b>\nAll existing links have been invalidated.")
    await callback.answer("Database wiped.")

@router.callback_query(F.data == "admin_clear_specific")
async def process_clear_specific(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(BotStates.waiting_for_delete_link)
    await callback.message.answer("🔗 <b>Send me the special link</b> of the file you want to delete:")
    await callback.answer()

@router.message(BotStates.waiting_for_delete_link)
async def process_delete_link(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    # Safely cancel out of delete mode if the admin types /cancel
    if message.text and message.text.lower() == '/cancel':
        await state.clear()
        await message.answer("🚫 <b>Action cancelled. Exited delete mode.</b>")
        return

    try:
        # Extract the unique ID from the link
        link_id = message.text.split("?start=")[-1]
        
        cursor.execute('SELECT message_id FROM files WHERE link_id = ?', (link_id,))
        result = cursor.fetchone()
        
        if result:
            msg_id = result[0]
            # Delete from channel
            await bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
            # Delete from DB
            cursor.execute('DELETE FROM files WHERE link_id = ?', (link_id,))
            conn.commit()
            
            await message.answer("✅ <b>File permanently deleted</b> from the channel and database.")
        else:
            await message.answer("❌ <b>Link not found in database.</b>")
            
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
    
    # Start the keep-alive web server
    asyncio.create_task(web_server())
    
    # Start the bot
    print("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
        
