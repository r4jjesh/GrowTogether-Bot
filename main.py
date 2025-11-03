import os
import asyncio
import logging
import sqlite3
from datetime import datetime
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from keep_alive import keep_alive

# === CONFIG ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("GrowTogether")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {5002083764, 1835452655, 5838038047, 2112909022}

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not found!")

# === DATABASE SETUP ===
DB_PATH = "tasks.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche TEXT,
    platform TEXT,
    name TEXT NOT NULL,
    points INTEGER NOT NULL,
    verification TEXT DEFAULT 'manual',
    url TEXT DEFAULT NULL
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS user_progress (
    user_id INTEGER,
    username TEXT,
    task_id INTEGER,
    completed INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0,
    proof TEXT DEFAULT NULL
)
""")
conn.commit()

# === COMMANDS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>👋 Welcome to Crypto Growth Bot!</b>\n\n"
        "💰 Complete crypto-related tasks, earn points, and climb the leaderboard!\n\n"
        "<b>Commands:</b>\n"
        "🧾 /list_tasks — View tasks\n"
        "🏆 /leaderboard — See top users\n"
        "📊 /my_stats — Check your points\n"
        "🧠 /complete_task [task_id] — Mark a task as complete"
    )
    await update.message.reply_text(text, parse_mode="HTML")

# (rest of your functions unchanged…)

# === MAIN ===
async def main():
    logger.info("🚀 Starting GrowTogether bot")
    keep_alive()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_task", add_task))
    app.add_handler(CommandHandler("remove_task", remove_task))
    app.add_handler(CommandHandler("list_tasks", list_tasks))
    app.add_handler(CommandHandler("my_stats", my_stats))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("review_proofs", review_proofs))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
