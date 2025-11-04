import os
import logging
import sqlite3
import threading
from html import escape

from flask import Flask, request, abort

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

# -------------------------------------------------
# CONFIG & LOGGING
# -------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("GrowTogether")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in environment!")

ADMIN_IDS = {5002083764, 1835452655, 5838038047, 2112909022}

# -------------------------------------------------
# DATABASE
# -------------------------------------------------
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

# -------------------------------------------------
# TELEGRAM HANDLERS
# -------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Welcome to Crypto Growth Bot!</b>\n\n"
        "Complete crypto-related tasks, earn points, and climb the leaderboard!\n\n"
        "<b>Commands:</b>\n"
        "/list_tasks — View tasks\n"
        "/leaderboard — See top users\n"
        "/my_stats — Check your points\n"
        "/complete_task [task_id] — Mark a task as complete"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Only admins can add tasks.")
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
        await update.message.reply_text("Points must be a number.")
        return

    cur.execute(
        "INSERT INTO tasks (niche, platform, name, points, url) VALUES (?, ?, ?, ?, ?)",
        (niche, platform, task_name, points, url),
    )
    conn.commit()

    await update.message.reply_text(
        f"<b>Task added!</b>\n\n"
        f"Niche: <b>{escape(niche)}</b>\n"
        f"Platform: <b>{escape(platform)}</b>\n"
        f"Task: <b>{escape(task_name)}</b>\n"
        f"URL: <a href='{escape(url)}'>{escape(url)}</a>\n"
        f"Points: <b>{points}</b>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Only admins can remove tasks.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /remove_task [task_id]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid task ID.")
        return

    cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    await update.message.reply_text(f"Task #{task_id} removed successfully.")

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    niche = context.args[0] if context.args else "crypto"
    cur.execute("SELECT id, platform, name, points, url FROM tasks WHERE niche = ?", (niche,))
    rows = cur.fetchall()

    if not rows:
        await update.message.reply_text(f"No tasks found for niche: {escape(niche)}")
        return

    for task_id, platform, name, points, url in rows:
        buttons = [[
            InlineKeyboardButton(f"Complete #{task_id}", callback_data=f"complete_{task_id}"),
            InlineKeyboardButton(f"Submit Proof", callback_data=f"proof_{task_id}")
        ]]
        if url:
            buttons.append([InlineKeyboardButton("Open Task", url=url)])
        if update.effective_user.id in ADMIN_IDS:
            buttons.append([InlineKeyboardButton(f"Remove #{task_id}", callback_data=f"remove_{task_id}")])

        markup = InlineKeyboardMarkup(buttons)

        await update.message.reply_text(
            f"<b>Task #{task_id}</b>\n"
            f"Platform: <b>{escape(platform)}</b>\n"
            f"{escape(name)}\n"
            f"Points: <b>{points}</b>",
            reply_markup=markup,
            parse_mode="HTML",
        )

# Proof system
proof_waiting = {}

async def ask_proof(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int):
    user_id = update.effective_user.id
    proof_waiting[user_id] = task_id
    await update.callback_query.message.reply_text(f"Please send your proof image for Task #{task_id}.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in proof_waiting:
        return
    task_id = proof_waiting.pop(user.id)
    photo = update.message.photo[-1]
    file_id = photo.file_id
    cur.execute(
        "INSERT OR REPLACE INTO user_progress (user_id, username, task_id, proof, completed, points) "
        "VALUES (?, ?, ?, ?, 0, 0)",
        (user.id, user.username or user.first_name, task_id, file_id)
    )
    conn.commit()
    await update.message.reply_text("Proof submitted! Awaiting admin review.")

async def review_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Only admins can review proofs.")
        return

    cur.execute("""
        SELECT user_id, username, task_id, proof FROM user_progress
        WHERE proof IS NOT NULL AND completed = 0
    """)
    rows = cur.fetchall()

    if not rows:
        await update.message.reply_text("No pending proofs.")
        return

    for user_id, username, task_id, proof_id in rows:
        name_display = f"@{username}" if username else str(user_id)
        buttons = [[
            InlineKeyboardButton("Approve", callback_data=f"approve_{user_id}_{task_id}"),
            InlineKeyboardButton("Reject", callback_data=f"reject_{user_id}_{task_id}")
        ]]
        markup = InlineKeyboardMarkup(buttons)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=proof_id,
            caption=f"Proof from {name_display} for Task #{task_id}",
            reply_markup=markup
        )

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
            await query.edit_message_text("You are not authorized.")
            return
        cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        await query.edit_message_text(f"Task #{task_id} removed successfully.")

    elif data.startswith("approve_"):
        _, user_id, task_id = data.split("_")
        user_id, task_id = int(user_id), int(task_id)
        cur.execute("SELECT points FROM tasks WHERE id=?", (task_id,))
        points = cur.fetchone()
        if points:
            cur.execute("""
                UPDATE user_progress SET completed = 1, points = ?
                WHERE user_id = ? AND task_id = ?
            """, (points[0], user_id, task_id))
            conn.commit()
            await query.edit_message_caption(
                caption=f"Approved! Awarded {points[0]} pts to user {user_id}."
            )

    elif data.startswith("reject_"):
        _, user_id, task_id = data.split("_")
        user_id, task_id = int(user_id), int(task_id)
        cur.execute("DELETE FROM user_progress WHERE user_id=? AND task_id=?", (user_id, task_id))
        conn.commit()
        await query.edit_message_caption("Rejected proof.")

async def process_completion(update, context, task_id, from_button=False):
    user = update.effective_user
    cur.execute("SELECT points FROM tasks WHERE id = ?", (task_id,))
    task = cur.fetchone()
    if not task:
        msg = "Task not found."
    else:
        cur.execute("SELECT completed FROM user_progress WHERE user_id=? AND task_id=?", (user.id, task_id))
        row = cur.fetchone()
        if row and row[0] == 1:
            msg = "You already completed this task!"
        else:
            cur.execute("""
                INSERT OR REPLACE INTO user_progress (user_id, username, task_id, completed, points)
                VALUES (?, ?, ?, 0, 0)
            """, (user.id, user.username or user.first_name, task_id))
            conn.commit()
            msg = f"Task #{task_id} marked as in-progress.\nPlease submit proof for admin approval!"

    if from_button:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT SUM(points) FROM user_progress WHERE user_id = ?", (update.effective_user.id,))
    total = cur.fetchone()[0] or 0
    await update.message.reply_text(f"Your total points: {total}")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("""
        SELECT username, SUM(points) as total
        FROM user_progress
        GROUP BY user_id
        ORDER BY total DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("No leaderboard data yet.")
        return
    lines = ["<b>Top 10 Players:</b>"]
    for i, (username, pts) in enumerate(rows, 1):
        name = f"@{username}" if username else "Anonymous"
        lines.append(f"{i}. {name} — {pts} pts")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# -------------------------------------------------
# FLASK APP
# -------------------------------------------------
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "GrowTogether Bot is alive and running!"

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(400)
    json_data = request.get_json()
    update = Update.de_json(json_data, application.bot)
    # Run in background
    import asyncio
    loop = asyncio.get_event_loop()
    loop.create_task(application.process_update(update))
    return "", 200

# -------------------------------------------------
# PTB APPLICATION
# -------------------------------------------------
application = ApplicationBuilder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("add_task", add_task))
application.add_handler(CommandHandler("remove_task", remove_task))
application.add_handler(CommandHandler("list_tasks", list_tasks))
application.add_handler(CommandHandler("my_stats", my_stats))
application.add_handler(CommandHandler("leaderboard", leaderboard))
application.add_handler(CommandHandler("review_proofs", review_proofs))
application.add_handler(CommandHandler("complete_task", lambda u, c: process_completion(u, c, int(c.args[0]) if c.args else None, from_button=False)))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# -------------------------------------------------
# STARTUP
# -------------------------------------------------
async def set_webhook():
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        logger.error("RENDER_EXTERNAL_URL not set!")
        return
    webhook_url = f"{url.rstrip('/')}/webhook"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook set to {webhook_url}")

async def startup():
    keep_alive()
    await application.initialize()
    await application.start()
    await set_webhook()
    logger.info("Bot initialized and webhook active!")

# -------------------------------------------------
# RUN
# -------------------------------------------------
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    # Run startup
    import asyncio
    asyncio.run(startup())

    # Start Flask
    port = int(os.getenv("PORT", 10000))
    logger.info(f"Starting Flask server on 0.0.0.0:{port}")
    flask_app.run(host="0.0.0.0", port=port)
