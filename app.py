"""
Author: Calixte Mayoraz
"""
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CallbackContext, CommandHandler, MessageHandler, filters
from credentials import BOT_TOKEN, BOT_USERNAME, WEBAPP_URL
import json
import sqlite3
import time # For timestamps

# Database file name
DB_NAME = 'bot_data.db'
MINING_DURATION_SECONDS = 100 * 60 # 100 minutes in seconds
CCC_PER_SESSION = 1.0 # 1 CCC token per session

def init_db():
    """Initializes the SQLite database."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_username TEXT UNIQUE NOT NULL,
                hedera_account_id TEXT UNIQUE,
                ccc_tokens REAL DEFAULT 0.0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mining_sessions (
                user_id INTEGER NOT NULL,
                start_time REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        conn.commit()

async def get_user_data(username: str):
    """Retrieves user data from the database."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, telegram_username, hedera_account_id, ccc_tokens FROM users WHERE telegram_username = ?", (username,))
        return cursor.fetchone()

async def create_user(username: str):
    """Registers a new user in the database."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (telegram_username, ccc_tokens) VALUES (?, ?)", (username, 0.0))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # User already exists
            return None

async def update_hedera_account(user_id: int, hedera_account_id: str):
    """Updates a user's Hedera account ID."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE users SET hedera_account_id = ? WHERE id = ?", (hedera_account_id, user_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Hedera account ID already linked to another user
            return False

async def get_active_mining_session(user_id: int):
    """Checks if a user has an active mining session."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT start_time FROM mining_sessions WHERE user_id = ?", (user_id,))
        session = cursor.fetchone()
        if session:
            start_time = session[0]
            elapsed_time = time.time() - start_time
            if elapsed_time < MINING_DURATION_SECONDS:
                return start_time
            else:
                # Session ended, clean up and award tokens
                await complete_mining_session(user_id, start_time)
                return None
        return None

async def start_mining_session(user_id: int):
    """Starts a new mining session for a user."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO mining_sessions (user_id, start_time) VALUES (?, ?)", (user_id, time.time()))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Should not happen if get_active_mining_session is called first
            return False

async def complete_mining_session(user_id: int, session_start_time: float):
    """Completes a mining session and awards CCC tokens."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # Remove the completed session
        cursor.execute("DELETE FROM mining_sessions WHERE user_id = ? AND start_time = ?", (user_id, session_start_time))
        # Award tokens
        cursor.execute("UPDATE users SET ccc_tokens = ccc_tokens + ? WHERE id = ?", (CCC_PER_SESSION, user_id))
        conn.commit()
        print(f"User {user_id} completed a mining session and earned {CCC_PER_SESSION} CCC.")


async def launch_web_ui(update: Update, callback: CallbackContext):
    username = update.effective_user.username
    if not username:
        await update.message.reply_text("Please set a Telegram username to use this bot.")
        return

    user_data = await get_user_data(username)
    if not user_data:
        # Register new user if they don't exist
        user_id = await create_user(username)
        if user_id:
            await update.message.reply_text("Welcome! You've been registered. Now let's open the app.")
            user_data = await get_user_data(username) # Reload user data including new id
        else:
            await update.message.reply_text("Could not register you. Please try again.")
            return

    # Pass user data to the web app
    webapp_data = {
        "userId": user_data[0],
        "username": user_data[1],
        "hederaAccountId": user_data[2],
        "cccTokens": user_data[3]
    }

    # Check for active mining session
    active_session_start_time = await get_active_mining_session(user_data[0])
    if active_session_start_time:
        webapp_data["miningSessionStart"] = active_session_start_time
        webapp_data["miningDuration"] = MINING_DURATION_SECONDS
    else:
        webapp_data["miningSessionStart"] = None
        webapp_data["miningDuration"] = MINING_DURATION_SECONDS

    kb = [
        [KeyboardButton(
            "Start App",
            web_app=WebAppInfo(WEBAPP_URL + f"?data={json.dumps(webapp_data)}")
        )]
    ]
    await update.message.reply_text("Let's do this...", reply_markup=ReplyKeyboardMarkup(kb))

async def web_app_data(update: Update, context: CallbackContext):
    data_str = update.message.web_app_data.data
    data = json.loads(data_str)

    user_id = data.get("userId")
    if not user_id:
        await update.message.reply_text("Error: User ID not found in web app data.")
        return

    action = data.get("action")

    if action == "link_hedera":
        hedera_account_id = data.get("hederaAccountId")
        if hedera_account_id:
            if await update_hedera_account(user_id, hedera_account_id):
                await update.message.reply_text(f"Your Hedera Account ID '{hedera_account_id}' has been linked!")
            else:
                await update.message.reply_text("Failed to link Hedera Account ID. It might already be linked to another user.")
        else:
            await update.message.reply_text("No Hedera Account ID provided.")
    elif action == "start_mining":
        if not await get_active_mining_session(user_id):
            if await start_mining_session(user_id):
                await update.message.reply_text("Mining session started! You'll earn 1 CCC in 100 minutes.")
            else:
                await update.message.reply_text("Failed to start mining session.")
        else:
            await update.message.reply_text("You already have an active mining session.")
    else:
        await update.message.reply_text(f"Received data: {data_str}")


if __name__ == '__main__':
    # Initialize the database
    init_db()

    # when we run the script we want to first create the bot from the token:
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # and let's set a command listener for /start to trigger our Web UI
    application.add_handler(CommandHandler('start', launch_web_ui))

    # as well as a web-app listener for the user-inputted data
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    # and send the bot on its way!
    print(f"Your bot is listening! Navigate to http://t.me/{BOT_USERNAME} to interact with it!")
    application.run_polling()