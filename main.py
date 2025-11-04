# -------------------------------------------------
# IMPORTS (everything you need)
# -------------------------------------------------
import os
import logging
import sqlite3
import asyncio
import threading
from queue import Queue
from html import escape

from flask import Flask, request, abort
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    Filters,
    CallbackContext,
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
    raise ValueError("BOT_TOKEN not found! Set it in Render → Environment")

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
# HANDLERS (v13 style – sync, CallbackContext)
# -------------------------------------------------
def start(update: Update, context: CallbackContext):
    text = (
        "<b>Welcome to Crypto Growth Bot!</b>\n\n"
        "Complete crypto-related tasks, earn points, and climb the leaderboard!\n\n"
        "<b>Commands:</b>\n"
        "/list_tasks — View tasks\n"
        "/leaderboard — See top users\n"
        "/my_stats — Check your points\n"
        "/complete_task [task_id] — Mark a task as complete"
    )
    update.message.reply_text(text, parse_mode="HTML")


def add_task(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("Only admins can add tasks.")
        return
    if len(context.args) < 5:
        update.message.reply_text(
            "<code>/add_task [niche] [platform] [name] [url] [points]</code>",
            parse_mode="HTML"
        )
        return
    niche, platform = context.args[0], context.args[1]
    name = " ".join(context.args[2:-2])
    url, points = context.args[-2], int(context.args[-1])
    cur.execute(
        "INSERT INTO tasks (niche, platform, name, points, url) VALUES (?, ?, ?, ?, ?)",
        (niche, platform, name, points, url)
    )
    conn.commit()
    update.message.reply_text(f"Task #{cur.lastrowid} added!")


def remove_task(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("Only admins can remove tasks.")
        return
    if not context.args:
        update.message.reply_text("Usage: /remove_task [task_id]")
        return
    task_id = int(context.args[0])
    cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    update.message.reply_text(f"Task #{task_id} removed.")


def list_tasks(update: Update, context: CallbackContext):
    niche = context.args[0] if context.args else "crypto"
    cur.execute(
        "SELECT id, platform, name, points, url FROM tasks WHERE niche = ?", (niche,)
    )
    rows = cur.fetchall()
    if not rows:
        update.message.reply_text(f"No tasks in {niche}")
        return
    for tid, plat, name, pts, url in rows:
        btns = [
            [InlineKeyboardButton(f"Complete #{tid}", callback_data=f"complete_{tid}")],
            [InlineKeyboardButton("Submit Proof", callback_data=f"proof_{tid}")],
        ]
        if url:
            btns.append([InlineKeyboardButton("Open", url=url)])
        if update.effective_user.id in ADMIN_IDS:
            btns.append([InlineKeyboardButton("Remove", callback_data=f"remove_{tid}")])
        update.message.reply_text(
            f"<b>Task #{tid}</b>\n{plat}: {escape(name)}\nPoints: {pts}",
            reply_markup=InlineKeyboardMarkup(btns),
            parse_mode="HTML",
        )


proof_waiting = {}


def ask_proof(update: Update, context: CallbackContext, task_id: int):
    proof_waiting[update.effective_user.id] = task_id
    update.callback_query.message.reply_text(f"Send proof for Task #{task_id}")


def handle_photo(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in proof_waiting:
        return
    task_id = proof_waiting.pop(user_id)
    file_id = update.message.photo[-1].file_id
    cur.execute(
        "INSERT OR REPLACE INTO user_progress (user_id, task_id, proof, completed) VALUES (?, ?, ?, 0)",
        (user_id, task_id, file_id),
    )
    conn.commit()
    update.message.reply_text("Proof submitted!")


def review_proofs(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_IDS:
        return
    cur.execute(
        "SELECT user_id, task_id, proof FROM user_progress WHERE proof IS NOT NULL AND completed = 0"
    )
    rows = cur.fetchall()
    if not rows:
        update.message.reply_text("No proofs.")
        return
    for uid, tid, fid in rows:
        context.bot.send_photo(
            update.effective_chat.id,
            fid,
            caption=f"Proof for Task #{tid} from user {uid}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Approve", callback_data=f"approve_{uid}_{tid}"
                        ),
                        InlineKeyboardButton(
                            "Reject", callback_data=f"reject_{uid}_{tid}"
                        ),
                    ]
                ]
            ),
        )


def button_handler(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    data = q.data
    if data.startswith("complete_"):
        process_completion(update, context, int(data.split("_")[1]), True)
    elif data.startswith("proof_"):
        ask_proof(update, context, int(data.split("_")[1]))
    elif data.startswith("remove_") and update.effective_user.id in ADMIN_IDS:
        tid = int(data.split("_")[1])
        cur.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        conn.commit()
        q.edit_message_text(f"Task #{tid} removed.")
    elif data.startswith("approve_"):
        _, uid, tid = data.split("_")
        uid, tid = int(uid), int(tid)
        cur.execute("SELECT points FROM tasks WHERE id = ?", (tid,))
        pts = cur.fetchone()[0]
        cur.execute(
            "UPDATE user_progress SET completed = 1, points = ? WHERE user_id = ? AND task_id = ?",
            (pts, uid, tid),
        )
        conn.commit()
        q.edit_message_caption(caption=f"Approved! +{pts} pts")
    elif data.startswith("reject_"):
        _, uid, tid = data.split("_")
        cur.execute(
            "DELETE FROM user_progress WHERE user_id = ? AND task_id = ?",
            (int(uid), int(tid)),
        )
        conn.commit()
        q.edit_message_caption("Rejected.")


def process_completion(update, context, task_id, from_button=False):
    user = update.effective_user
    cur.execute(
        "SELECT completed FROM user_progress WHERE user_id = ? AND task_id = ?",
        (user.id, task_id),
    )
    row = cur.fetchone()
    if row and row[0] == 1:
        msg = "Already completed!"
    else:
        cur.execute(
            "INSERT OR REPLACE INTO user_progress (user_id, task_id, completed) VALUES (?, ?, 0)",
            (user.id, task_id),
        )
        conn.commit()
        msg = f"Task #{task_id} in progress. Submit proof!"
    if from_button:
        update.callback_query.edit_message_text(msg)
    else:
        update.message.reply_text(msg)


def my_stats(update: Update, context: CallbackContext):
    cur.execute(
        "SELECT SUM(points) FROM user_progress WHERE user_id = ? AND completed = 1",
        (update.effective_user.id,),
    )
    pts = cur.fetchone()[0] or 0
    update.message.reply_text(f"Your points: {pts}")


def leaderboard(update: Update, context: CallbackContext):
    cur.execute(
        "SELECT username, SUM(points) FROM user_progress WHERE completed = 1 GROUP BY user_id ORDER BY SUM(points) DESC LIMIT 10"
    )
    rows = cur.fetchall()
    if not rows:
        update.message.reply_text("No data.")
        return
    text = "<b>Leaderboard</b>\n" + "\n".join(
        f"{i+1}. @{r[0] or 'User'} — {r[1]} pts" for i, r in enumerate(rows)
    )
    update.message.reply_text(text, parse_mode="HTML")


def complete_task(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("Usage: /complete_task [task_id]")
        return
    process_completion(update, context, int(context.args[0]), False)


# -------------------------------------------------
# FLASK WEBHOOK (Render needs a route)
# -------------------------------------------------
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!"

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(400)
    json_data = request.get_json()
    update = Update.de_json(json_data, updater.bot)
    updater.dispatcher.process_update(update)
    return "", 200


# -------------------------------------------------
# MAIN – start keep-alive + webhook
# -------------------------------------------------
def main():
    global updater
    updater = Updater(BOT_TOKEN, use_context=True)

    dp = updater.dispatcher

    # Register handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("add_task", add_task))
    dp.add_handler(CommandHandler("remove_task", remove_task))
    dp.add_handler(CommandHandler("list_tasks", list_tasks))
    dp.add_handler(CommandHandler("my_stats", my_stats))
    dp.add_handler(CommandHandler("leaderboard", leaderboard))
    dp.add_handler(CommandHandler("review_proofs", review_proofs))
    dp.add_handler(CommandHandler("complete_task", complete_task))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(MessageHandler(Filters.photo, handle_photo))

    # Keep-alive (prevents Render free-tier sleep)
    keep_alive()

    # Webhook URL from Render
    webhook_url = os.getenv("RENDER_EXTERNAL_URL")
    port = int(os.getenv("PORT", 10000))

    if webhook_url:
        webhook_url = f"{webhook_url.rstrip('/')}/webhook"
        updater.start_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="/webhook",
            webhook_url=webhook_url,
        )
        logger.info(f"Webhook set to {webhook_url}")
    else:
        logger.warning("RENDER_EXTERNAL_URL not set – falling back to polling")
        updater.start_polling()

    logger.info("Bot is LIVE!")

    # Run Flask in a background thread (Render expects a web server)
    def run_flask():
        flask_app.run(host="0.0.0.0", port=port)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Keep the main thread alive
    updater.idle()


# -------------------------------------------------
# ENTRYPOINT
# -------------------------------------------------
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    main()
