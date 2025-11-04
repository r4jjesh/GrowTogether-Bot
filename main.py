# main.py
import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Database setup
DB_NAME = "crypto_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            points INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT,
            reward INTEGER,
            type TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_tasks (
            user_id INTEGER,
            task_id INTEGER,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (task_id) REFERENCES tasks (id)
        )
    ''')
    # Insert default tasks if none exist
    c.execute("SELECT COUNT(*) FROM tasks")
    if c.fetchone()[0] == 0:
        tasks = [
            ("X: RT+like ❤️🔁", 100, "rt_like"),
            ("X: RT+like ❤️🔁", 100, "rt_like"),
            ("Follow @CryptoGrowthX on X", 150, "follow"),
            ("Join Telegram Channel", 200, "join")
        ]
        c.executemany("INSERT INTO tasks (description, reward, type) VALUES (?, ?, ?)", tasks)
    conn.commit()
    conn.close()

# Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, points) VALUES (?, ?, 0)", (user.id, user.username))
    conn.commit()
    conn.close()

    keyboard = [
        [InlineKeyboardButton("View Tasks", callback_data="list_tasks")],
        [InlineKeyboardButton("My Stats", callback_data="my_stats")],
        [InlineKeyboardButton("Leaderboard", callback_data="leaderboard")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        "👋 *Welcome to Crypto Growth Bot!*\n\n"
        "💰 Complete crypto-related tasks, earn points, and climb the leaderboard!\n\n"
        "📋 *Commands:*\n"
        "/list_tasks — View tasks\n"
        "🏆 /leaderboard — See top users\n"
        "📊 /my_stats — Check your points\n"
        "✅ /complete_task [task_id] — Mark a task as complete"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=reply_markup)

# List Tasks
async def list_tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await list_tasks(update, context)

async def list_tasks_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await list_tasks(query, context)

async def list_tasks(update_or_query, context):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, description, reward FROM tasks")
    tasks = c.fetchall()
    conn.close()

    if not tasks:
        text = "No tasks available yet!"
    else:
        text = "*Available Tasks:*\n\n"
        for task in tasks:
            task_id, desc, reward = task
            text += f"*{task_id}*: {desc}\n*Reward:* {reward} pts\n\n"

    keyboard = [[InlineKeyboardButton("Refresh", callback_data="list_tasks")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if isinstance(update_or_query, Update):
        await update_or_query.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await update_or_query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)

# My Stats
async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id = ?", (user.id,))
    result = c.fetchone()
    points = result[0] if result else 0
    conn.close()

    text = f"📊 *Your Stats*\n\n*Points:* {points} pts"
    keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="start_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

# Leaderboard
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username, points FROM users ORDER BY points DESC LIMIT 10")
    leaders = c.fetchall()
    conn.close()

    if not leaders:
        text = "🏆 No one on the leaderboard yet!"
    else:
        text = "🏆 *Leaderboard - Top 10*\n\n"
        for i, (username, points) in enumerate(leaders, 1):
            name = f"@{username}" if username else "Anonymous"
            text += f"{i}. {name} — {points} pts\n"

    keyboard = [[InlineKeyboardButton("Back", callback_data="start_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

# Complete Task
async def complete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /complete_task [task_id]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Task ID must be a number.")
        return

    user = update.effective_user
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Check if task exists
    c.execute("SELECT id, reward FROM tasks WHERE id = ?", (task_id,))
    task = c.fetchone()
    if not task:
        await update.message.reply_text("Task not found.")
        conn.close()
        return

    # Check if already completed
    c.execute("SELECT completed FROM user_tasks WHERE user_id = ? AND task_id = ?", (user.id, task_id))
    completed = c.fetchone()
    if completed and completed[0] == 1:
        await update.message.reply_text("You already completed this task!")
        conn.close()
        return

    # Mark as complete and add points
    reward = task[1]
    c.execute("INSERT OR IGNORE INTO user_tasks (user_id, task_id, completed) VALUES (?, ?, 1)", (user.id, task_id))
    c.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (reward, user.id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"Task #{task_id} completed! +{reward} pts")

# Button Handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "list_tasks":
        await list_tasks_query(update, context)
    elif query.data == "my_stats":
        await my_stats_inline(update, context)
    elif query.data == "leaderboard":
        await leaderboard_inline(update, context)
    elif query.data == "start_menu":
        await start_inline(update, context)

async def my_stats_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id = ?", (user.id,))
    result = c.fetchone()
    points = result[0] if result else 0
    conn.close()

    text = f"📊 *Your Stats*\n\n*Points:* {points} pts"
    keyboard = [[InlineKeyboardButton("Back", callback_data="start_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def leaderboard_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await leaderboard(update, context)  # Reuse same function

async def start_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("View Tasks", callback_data="list_tasks")],
        [InlineKeyboardButton("My Stats", callback_data="my_stats")],
        [InlineKeyboardButton("Leaderboard", callback_data="leaderboard")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        "👋 *Welcome to Crypto Growth Bot!*\n\n"
        "💰 Complete crypto-related tasks, earn points, and climb the leaderboard!\n\n"
        "📋 *Commands:*\n"
        "/list_tasks — View tasks\n"
        "🏆 /leaderboard — See top users\n"
        "📊 /my_stats — Check your points\n"
        "✅ /complete_task [task_id] — Mark a task as complete"
    )
    await query.edit_message_text(welcome_text, parse_mode='Markdown', reply_markup=reply_markup)

# Error Handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# Main
def main():
    init_db()
    application = Application.builder().token("YOUR_BOT_TOKEN_HERE").build()

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list_tasks", list_tasks_cmd))
    application.add_handler(CommandHandler("my_stats", my_stats))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("complete_task", complete_task))

    # Buttons
    application.add_handler(CallbackQueryHandler(button_handler))

    # Errors
    application.add_error_handler(error_handler)

    # Start bot
    print("Crypto Growth Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
