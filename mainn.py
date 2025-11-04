import os
import logging
import sqlite3
from html import escape
import asyncio

from flask import Flask, request, abort
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

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("GrowTogether")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found!")

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
# HANDLERS
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
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Only admins can add tasks.")
        return
    if len(context.args) < 5:
        await update.message.reply_text(
            "<code>/add_task [niche] [platform] [name] [url] [points]</code>",
            parse_mode="HTML"
        )
        return
    niche, platform = context.args[0], context.args[1]
    name = " ".join(context.args[2:-2])
    url, points = context.args[-2], int(context.args[-1])
    cur.execute("INSERT INTO tasks (niche, platform, name, points, url) VALUES (?, ?, ?, ?, ?)",
                (niche, platform, name, points, url))
    conn.commit()
    await update.message.reply_text(f"Task #{cur.lastrowid} added!")

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Only admins can remove tasks.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove_task [task_id]")
        return
    task_id = int(context.args[0])
    cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    await update.message.reply_text(f"Task #{task_id} removed.")

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    niche = context.args[0] if context.args else "crypto"
    cur.execute("SELECT id, platform, name, points, url FROM tasks WHERE niche = ?", (niche,))
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text(f"No tasks in {niche}")
        return
    for tid, plat, name, pts, url in rows:
        btns = [
            [InlineKeyboardButton(f"Complete #{tid}", callback_data=f"complete_{tid}")],
            [InlineKeyboardButton("Submit Proof", callback_data=f"proof_{tid}")]
        ]
        if url:
            btns.append([InlineKeyboardButton("Open", url=url)])
        if update.effective_user.id in ADMIN_IDS:
            btns.append([InlineKeyboardButton("Remove", callback_data=f"remove_{tid}")])
        await update.message.reply_text(
            f"<b>Task #{tid}</b>\n{plat}: {escape(name)}\nPoints: {pts}",
            reply_markup=InlineKeyboardMarkup(btns),
            parse_mode="HTML"
        )

proof_waiting = {}

async def ask_proof(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int):
    proof_waiting[update.effective_user.id] = task_id
    await update.callback_query.message.reply_text(f"Send proof for Task #{task_id}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in proof_waiting:
        return
    task_id = proof_waiting.pop(user_id)
    file_id = update.message.photo[-1].file_id
    cur.execute(
        "INSERT OR REPLACE INTO user_progress (user_id, task_id, proof, completed) VALUES (?, ?, ?, 0)",
        (user_id, task_id, file_id)
    )
    conn.commit()
    await update.message.reply_text("Proof submitted!")

async def review_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    cur.execute("SELECT user_id, task_id, proof FROM user_progress WHERE proof IS NOT NULL AND completed = 0")
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("No proofs.")
        return
    for uid, tid, fid in rows:
        await context.bot.send_photo(
            update.effective_chat.id,
            fid,
            caption=f"Proof for Task #{tid} from user {uid}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Approve", callback_data=f"approve_{uid}_{tid}"),
                InlineKeyboardButton("Reject", callback_data=f"reject_{uid}_{tid}")
            ]])
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data.startswith("complete_"):
        await process_completion(update, context, int(data.split("_")[1]), True)
    elif data.startswith("proof_"):
        await ask_proof(update, context, int(data.split("_")[1]))
    elif data.startswith("remove_") and update.effective_user.id in ADMIN_IDS:
        tid = int(data.split("_")[1])
        cur.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        conn.commit()
        await q.edit_message_text(f"Task #{tid} removed.")
    elif data.startswith("approve_"):
        _, uid, tid = data.split("_")
        uid, tid = int(uid), int(tid)
        cur.execute("SELECT points FROM tasks WHERE id = ?", (tid,))
        pts = cur.fetchone()[0]
        cur.execute("UPDATE user_progress SET completed = 1, points = ? WHERE user_id = ? AND task_id = ?", (pts, uid, tid))
        conn.commit()
        await q.edit_message_caption(caption=f"Approved! +{pts} pts")
    elif data.startswith("reject_"):
        _, uid, tid = data.split("_")
        cur.execute("DELETE FROM user_progress WHERE user_id = ? AND task_id = ?", (int(uid), int(tid)))
        conn.commit()
        await q.edit_message_caption("Rejected.")

async def process_completion(update, context, task_id, from_button=False):
    user = update.effective_user
    cur.execute("SELECT completed FROM user_progress WHERE user_id = ? AND task_id = ?", (user.id, task_id))
    row = cur.fetchone()
    if row and row[0] == 1:
        msg = "Already completed!"
    else:
        cur.execute("INSERT OR REPLACE INTO user_progress (user_id, task_id, completed) VALUES (?, ?, 0)", (user.id, task_id))
        conn.commit()
        msg = f"Task #{task_id} in progress. Submit proof!"
    if from_button:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT SUM(points) FROM user_progress WHERE user_id = ? AND completed = 1", (update.effective_user.id,))
    pts = cur.fetchone()[0] or 0
    await update.message.reply_text(f"Your points: {pts}")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT username, SUM(points) FROM user_progress WHERE completed = 1 GROUP BY user_id ORDER BY SUM(points) DESC LIMIT 10")
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("No data.")
        return
    text = "<b>Leaderboard</b>\n" + "\n".join(f"{i+1}. @{r[0] or 'User'} — {r[1]} pts" for i, r in enumerate(rows))
    await update.message.reply_text(text, parse_mode="HTML")

# -------------------------------------------------
# FLASK + WEBHOOK
# -------------------------------------------------
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!"

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(400)
    update = Update.de_json(request.get_json(), application.bot)
    asyncio.create_task(application.process_update(update))
    return "", 200

# -------------------------------------------------
# APPLICATION (NO UPDATER!)
# -------------------------------------------------
application = ApplicationBuilder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("add_task", add_task))
application.add_handler(CommandHandler("remove_task", remove_task))
application.add_handler(CommandHandler("list_tasks", list_tasks))
application.add_handler(CommandHandler("my_stats", my_stats))
application.add_handler(CommandHandler("leaderboard", leaderboard))
application.add_handler(CommandHandler("review_proofs", review_proofs))
application.add_handler(CommandHandler("complete_task", lambda u, c: process_completion(u, c, int(c.args[0]) if c.args else None)))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# -------------------------------------------------
# STARTUP
# -------------------------------------------------
async def set_webhook():
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        logger.error("RENDER_EXTERNAL_URL missing!")
        return
    webhook_url = f"{url.rstrip('/')}/webhook"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook: {webhook_url}")

async def main():
    keep_alive()
    await application.initialize()
    await application.start()
    await set_webhook()
    logger.info("Bot is LIVE!")

# -------------------------------------------------
# RUN
# -------------------------------------------------
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
    port = int(os.getenv("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
