import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from html import escape
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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

# Create tasks table
cur.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche TEXT,
    platform TEXT,
    name TEXT NOT NULL,
    points INTEGER NOT NULL,
    verification TEXT DEFAULT 'manual'
)
""")

# ✅ Add URL column if not exists
try:
    cur.execute("ALTER TABLE tasks ADD COLUMN url TEXT DEFAULT NULL")
    conn.commit()
except sqlite3.OperationalError:
    pass

# Create user_progress table
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


# === ADD TASK ===
async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Only admins can add tasks.")
        return

    if len(context.args) < 5:
        await update.message.reply_text(
            "Usage:\n<code>/add_task [niche] [platform] [task name] [url] [points]</code>\n"
            "Example:\n<code>/add_task crypto twitter Follow_XYZ https://twitter.com/xyz 10</code>",
            parse_mode="HTML",
        )
        return

    niche = context.args[0]
    platform = context.args[1]
    task_name = " ".join(context.args[2:-2])
    url = context.args[-2]
    try:
        points = int(context.args[-1])
    except ValueError:
        await update.message.reply_text("⚠️ Points must be a number.")
        return

    cur.execute(
        "INSERT INTO tasks (niche, platform, name, points, url) VALUES (?, ?, ?, ?, ?)",
        (niche, platform, task_name, points, url),
    )
    conn.commit()

    await update.message.reply_text(
        f"✅ <b>Task added!</b>\n\n"
        f"📂 Niche: <b>{escape(niche)}</b>\n"
        f"💬 Platform: <b>{escape(platform)}</b>\n"
        f"📝 Task: <b>{escape(task_name)}</b>\n"
        f"🔗 URL: <a href='{escape(url)}'>{escape(url)}</a>\n"
        f"💰 Points: <b>{points}</b>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# === REMOVE TASK ===
async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Only admins can remove tasks.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /remove_task [task_id]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid task ID.")
        return

    cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    await update.message.reply_text(f"🗑️ Task #{task_id} removed successfully.")


# === LIST TASKS ===
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    niche = context.args[0] if context.args else "crypto"
    cur.execute("SELECT id, platform, name, points, url FROM tasks WHERE niche = ?", (niche,))
    rows = cur.fetchall()

    if not rows:
        await update.message.reply_text(f"📭 No tasks found for niche: {escape(niche)}")
        return

    for task_id, platform, name, points, url in rows:
        buttons = [[
            InlineKeyboardButton(f"✅ Complete #{task_id}", callback_data=f"complete_{task_id}"),
            InlineKeyboardButton(f"📸 Submit Proof", callback_data=f"proof_{task_id}")
        ]]
        # Add URL button if task has one
        if url:
            buttons.append([InlineKeyboardButton("🔗 Open Task", url=url)])

        if update.effective_user.id in ADMIN_IDS:
            buttons.append([
                InlineKeyboardButton(f"🗑️ Remove #{task_id}", callback_data=f"remove_{task_id}")
            ])

        markup = InlineKeyboardMarkup(buttons)

        await update.message.reply_text(
            f"📋 <b>Task #{task_id}</b>\n"
            f"💬 Platform: <b>{escape(platform)}</b>\n"
            f"📝 {escape(name)}\n"
            f"💰 Points: <b>{points}</b>",
            reply_markup=markup,
            parse_mode="HTML",
        )


# === PROOF SUBMISSION ===
proof_waiting = {}

async def ask_proof(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int):
    user_id = update.effective_user.id
    proof_waiting[user_id] = task_id
    await update.callback_query.message.reply_text(
        f"📸 Please send your proof image for Task #{task_id}."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in proof_waiting:
        return
    task_id = proof_waiting.pop(user.id)
    photo = update.message.photo[-1]
    file_id = photo.file_id
    cur.execute(
        "INSERT OR REPLACE INTO user_progress (user_id, username, task_id, proof, completed, points) VALUES (?, ?, ?, ?, 0, 0)",
        (user.id, user.username or user.first_name, task_id, file_id)
    )
    conn.commit()
    await update.message.reply_text("✅ Proof submitted! Awaiting admin review.")


# === REVIEW PROOFS ===
async def review_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Only admins can review proofs.")
        return

    cur.execute("""
        SELECT user_id, username, task_id, proof FROM user_progress
        WHERE proof IS NOT NULL AND completed = 0
    """)
    rows = cur.fetchall()

    if not rows:
        await update.message.reply_text("📭 No pending proofs.")
        return

    for user_id, username, task_id, proof_id in rows:
        name_display = f"@{username}" if username else str(user_id)
        buttons = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}_{task_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}_{task_id}")
            ]
        ]
        markup = InlineKeyboardMarkup(buttons)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=proof_id,
            caption=f"🧾 Proof from {name_display} for Task #{task_id}",
            reply_markup=markup
        )


# === BUTTON HANDLER ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("complete_"):
        task_id = int(data.split("_")[1])
        await process_completion(update, context, task_id, from_button=True)

    elif data.startswith("proof_"):
        task_id = int(data.split("_")[1])
        await ask_proof(update, context, task_id)

    elif data.startswith("remove_"):
        task_id = int(data.split("_")[1])
        if update.effective_user.id not in ADMIN_IDS:
            await query.edit_message_text("🚫 You are not authorized.")
            return
        cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        await query.edit_message_text(f"🗑️ Task #{task_id} removed successfully.")

    elif data.startswith("approve_"):
        _, user_id, task_id = data.split("_")
        user_id, task_id = int(user_id), int(task_id)
        cur.execute("SELECT points FROM tasks WHERE id=?", (task_id,))
        points = cur.fetchone()
        if points:
            cur.execute("""
                UPDATE user_progress
                SET completed = 1, points = ?
                WHERE user_id = ? AND task_id = ?
            """, (points[0], user_id, task_id))
            conn.commit()
            await query.edit_message_caption(
                caption=f"✅ Approved! Awarded {points[0]} pts to user {user_id}."
            )

    elif data.startswith("reject_"):
        _, user_id, task_id = data.split("_")
        user_id, task_id = int(user_id), int(task_id)
        cur.execute("DELETE FROM user_progress WHERE user_id=? AND task_id=?", (user_id, task_id))
        conn.commit()
        await query.edit_message_caption("❌ Rejected proof.")


# === COMPLETE TASK ===
async def process_completion(update, context, task_id, from_button=False):
    user = update.effective_user
    cur.execute("SELECT points FROM tasks WHERE id = ?", (task_id,))
    task = cur.fetchone()
    if not task:
        msg = "❌ Task not found."
    else:
        cur.execute("SELECT completed FROM user_progress WHERE user_id=? AND task_id=?", (user.id, task_id))
        row = cur.fetchone()
        if row and row[0] == 1:
            msg = "✅ You already completed this task!"
        else:
            cur.execute("""
                INSERT OR REPLACE INTO user_progress (user_id, username, task_id, completed, points)
                VALUES (?, ?, ?, 0, 0)
            """, (user.id, user.username or user.first_name, task_id))
            conn.commit()
            msg = f"🕓 Task #{task_id} marked as completed.\n\n📸 Please submit proof for admin approval!"

    if from_button:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)


# === STATS + LEADERBOARD ===
async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT SUM(points) FROM user_progress WHERE user_id = ?", (update.effective_user.id,))
    total = cur.fetchone()[0] or 0
    await update.message.reply_text(f"🏅 Your total points: {total}")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("""
    SELECT username, SUM(points) as total_points
    FROM user_progress
    GROUP BY username
    ORDER BY total_points DESC
    LIMIT 10
    """)
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("📭 No leaderboard data yet.")
        return
    msg_lines = ["🏆 <b>Top 10 Players:</b>\n"]
    for i, (username, points) in enumerate(rows, 1):
        name_display = f"@{username}" if username else "Anonymous"
        msg_lines.append(f"{i}. {name_display} — {points} pts")
    await update.message.reply_text("\n".join(msg_lines), parse_mode="HTML")


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
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
