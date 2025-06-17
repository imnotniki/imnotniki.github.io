import os
import subprocess
import sqlite3
import time
import json  # Import json for handling WebApp data
from dotenv import load_dotenv
from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup  # Import necessary classes
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Load environment variables from .env file
load_dotenv()

# Get your Telegram bot token from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Make sure WEBAPP_URL is defined in your .env or directly here
WEBAPP_URL = os.getenv("WEBAPP_URL",
                       "YOUR_WEBAPP_URL_HERE")  # Replace YOUR_WEBAPP_URL_HERE with the actual URL to your index.html

# Define the fixed amount of CCC to send (for faucet, not mining directly)
CCC_AMOUNT = 1

# --- Database Configuration ---
DB_FILE = 'faucet.db'
COOLDOWN_MINUTES = 100
COOLDOWN_SECONDS = COOLDOWN_MINUTES * 60


# --- Database Helper Functions ---

def init_db():
    """
    Initializes the SQLite database.
    Creates the necessary tables if they don't already exist.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        # Faucet claims table (kept for original functionality if needed)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS faucet_claims (
                telegram_user_id INTEGER PRIMARY KEY,
                hedera_account_id TEXT NOT NULL,
                last_claim_timestamp REAL NOT NULL
            )
        ''')

        # Mining table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ccc_mining (
                telegram_user_id INTEGER PRIMARY KEY,
                last_mine_timestamp REAL NOT NULL DEFAULT 0.0,
                ccc_balance REAL NOT NULL DEFAULT 0.0,
                FOREIGN KEY (telegram_user_id) REFERENCES faucet_claims(telegram_user_id)
            )
        ''')

        conn.commit()
        conn.close()
        print(f"Database '{DB_FILE}' initialized successfully.")
    except sqlite3.Error as e:
        print(f"Error initializing database: {e}")


def get_user_data(telegram_user_id: int):
    """
    Fetches the Hedera account ID and last claim timestamp for a given Telegram user ID.
    (Kept for original faucet functionality)
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT hedera_account_id, last_claim_timestamp FROM faucet_claims WHERE telegram_user_id = ?",
            (telegram_user_id,)
        )
        result = cursor.fetchone()
        conn.close()
        return result
    except sqlite3.Error as e:
        print(f"Error fetching user data for {telegram_user_id}: {e}")
        return None


def add_user_claim(telegram_user_id: int, hedera_account_id: str, timestamp: float):
    """
    Inserts a new user record into the faucet_claims table.
    (Kept for original faucet functionality)
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO faucet_claims (telegram_user_id, hedera_account_id, last_claim_timestamp) VALUES (?, ?, ?)",
            (telegram_user_id, hedera_account_id, timestamp)
        )
        conn.commit()
        conn.close()
        return cursor.rowcount == 1
    except sqlite3.Error as e:
        print(f"Error adding new user claim for {telegram_user_id}: {e}")
        return False


def update_user_claim_timestamp(telegram_user_id: int, timestamp: float):
    """
    Updates the last claim timestamp for an existing user in faucet_claims.
    (Kept for original faucet functionality)
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE faucet_claims SET last_claim_timestamp = ? WHERE telegram_user_id = ?",
            (timestamp, telegram_user_id)
        )
        conn.commit()
        conn.close()
        return cursor.rowcount == 1
    except sqlite3.Error as e:
        print(f"Error updating user claim timestamp for {telegram_user_id}: {e}")
        return False


def get_mining_status(telegram_user_id: int):
    """
    Get the mining status for a user.

    Args:
        telegram_user_id (int): The Telegram user ID

    Returns:
        dict: Dictionary containing mining status and balance
    """
    conn = None  # Initialize conn to None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        # Get mining data
        cursor.execute('''
            SELECT last_mine_timestamp, ccc_balance 
            FROM ccc_mining 
            WHERE telegram_user_id = ?
        ''', (telegram_user_id,))

        result = cursor.fetchone()
        current_time = time.time()

        if result is None:
            # User not in mining table, initialize with 0 balance and 0 last_mine_timestamp
            cursor.execute('''
                INSERT INTO ccc_mining (telegram_user_id, last_mine_timestamp, ccc_balance)
                VALUES (?, ?, 0.0)
            ''', (telegram_user_id, 0.0))
            conn.commit()
            return {
                'can_mine': True,
                'time_remaining': 0,
                'balance': 0.0,
                'is_mining': False,
                'cooldown_duration': COOLDOWN_SECONDS
            }

        last_mine_time, balance = result
        time_since_last_mine = current_time - last_mine_time

        if time_since_last_mine >= COOLDOWN_SECONDS:
            return {
                'can_mine': True,
                'time_remaining': 0,
                'balance': balance,
                'is_mining': False,
                'cooldown_duration': COOLDOWN_SECONDS
            }
        else:
            return {
                'can_mine': False,
                'time_remaining': int(COOLDOWN_SECONDS - time_since_last_mine),
                'balance': balance,
                'is_mining': True,
                'cooldown_duration': COOLDOWN_SECONDS
            }

    except sqlite3.Error as e:
        print(f"Error getting mining status for {telegram_user_id}: {e}")
        return {
            'can_mine': False,
            'time_remaining': 0,
            'balance': 0.0,
            'is_mining': False,
            'cooldown_duration': COOLDOWN_SECONDS,
            'error': str(e)
        }
    finally:
        if conn:
            conn.close()


def process_mining(telegram_user_id: int) -> dict:
    """
    Process a mining request from a user.

    Args:
        telegram_user_id (int): The Telegram user ID

    Returns:
        dict: Result of the mining operation
    """
    conn = None  # Initialize conn to None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        current_time = time.time()

        status = get_mining_status(telegram_user_id)  # Uses the separate get_mining_status to check state

        if not status['can_mine']:
            return {
                'success': False,
                'message': f'Please wait {status["time_remaining"] // 60} minutes before mining again.',
                'balance': status['balance'],
                'time_remaining': status['time_remaining'],
                'can_mine': False,
                'cooldown_duration': COOLDOWN_SECONDS
            }

        # Calculate mining reward (1 CCC)
        reward = 1.0
        new_balance = status['balance'] + reward

        # Update mining record
        cursor.execute('''
            INSERT OR REPLACE INTO ccc_mining 
            (telegram_user_id, last_mine_timestamp, ccc_balance)
            VALUES (?, ?, ?)
        ''', (telegram_user_id, current_time, new_balance))

        conn.commit()

        return {
            'success': True,
            'message': f'Successfully mined {reward:.4f} CCC!',
            'balance': new_balance,
            'time_remaining': COOLDOWN_SECONDS,  # Reset cooldown for the UI
            'can_mine': False,
            'cooldown_duration': COOLDOWN_SECONDS
        }

    except sqlite3.Error as e:
        print(f"Error processing mining: {e}")
        return {
            'success': False,
            'message': f'Database error: {str(e)}',
            'balance': 0.0,
            'time_remaining': 0,
            'can_mine': False,
            'cooldown_duration': COOLDOWN_SECONDS,
            'error': str(e)
        }
    finally:
        if conn:
            conn.close()


# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends a welcome message to the user and displays the WebApp button.
    """
    kb = [
        [KeyboardButton(
            "Open CCC Miner",
            web_app=WebAppInfo(WEBAPP_URL)
        )]
    ]
    await update.message.reply_text(
        "Welcome to the CCC Miner! Click the button below to start mining.",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles data received from the WebApp.
    """
    user_id = update.effective_user.id
    try:
        received_data = json.loads(update.effective_message.web_app_data.data)
        action = received_data.get('action')

        if action == "get_mining_status":
            status = get_mining_status(user_id)
            # Send status back to WebApp
            await update.effective_message.reply_text(
                json.dumps({
                    'action': 'update_mining_status',
                    'balance': status['balance'],
                    'time_remaining': status['time_remaining'],
                    'can_mine': status['can_mine'],
                    'cooldown_duration': status['cooldown_duration']
                }),
                web_app=WebAppInfo(WEBAPP_URL)  # Important for sending data back to WebApp
            )
        elif action == "mine_ccc":
            # Hedera Account ID is not directly used for mining in this new setup,
            # but you might want to fetch it from the faucet_claims table if needed
            # For simplicity, we assume the user is already registered via faucet_claims or
            # we just track based on telegram_user_id for mining.
            # If a hedera_account_id is strictly required for mining, you'd need to adapt.

            # Here, we'll try to get the hedera_account_id from faucet_claims if it exists
            # Otherwise, for mining, we might proceed just with telegram_user_id
            user_faucet_data = get_user_data(user_id)
            hedera_account_id = user_faucet_data[0] if user_faucet_data else "N/A"  # Or handle new users

            mining_result = process_mining(user_id)
            # Send result back to WebApp
            await update.effective_message.reply_text(
                json.dumps({
                    'action': 'mining_result',
                    'success': mining_result['success'],
                    'message': mining_result['message'],
                    'balance': mining_result['balance'],
                    'time_remaining': mining_result['time_remaining'],
                    'can_mine': mining_result['can_mine'],
                    'cooldown_duration': mining_result['cooldown_duration']
                }),
                web_app=WebAppInfo(WEBAPP_URL)  # Important for sending data back to WebApp
            )
        else:
            await update.effective_message.reply_text(f"Unknown action: {action}")

    except json.JSONDecodeError:
        await update.effective_message.reply_text("Invalid data received from WebApp.")
    except Exception as e:
        await update.effective_message.reply_text(f"An error occurred processing WebApp data: {e}")
        print(f"Error in web_app_data: {e}")


# The send_hbar_to_account and echo handlers can remain for the original faucet functionality
# or be removed if you want a purely mining bot.
# For now, let's keep them as a fallback or for dual functionality.
async def send_hbar_to_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles incoming messages, validates the Hedera account ID, checks cooldown,
    and attempts to execute the Token transfer script.
    It also manages user data in the SQLite database.
    (Original faucet functionality)
    """
    user_input = update.message.text.strip()
    telegram_user_id = update.message.from_user.id

    # Basic validation for Hedera Account ID format (0.0.xxxxx)
    if not user_input.startswith("0.0.") or not user_input[4:].isdigit():
        await update.message.reply_text(
            "That doesn't look like a valid Hedera Account ID. "
            "Please send an ID in the format `0.0.123456`."
        )
        return

    recipient_account_id = user_input
    current_timestamp = time.time()  # Get current Unix timestamp

    user_data = get_user_data(telegram_user_id)

    can_proceed = False

    if user_data:
        stored_hedera_account_id, last_claim_timestamp = user_data

        if stored_hedera_account_id != recipient_account_id:
            await update.message.reply_text(
                "🚫 You have already claimed CCC for a different Hedera Account ID "
                f"(`{stored_hedera_account_id}`). "
                "Each Telegram user can only claim for one Hedera account. "
                "If you believe this is an error or need to change your linked account, "
                "please contact the bot administrator."
            )
            return

        time_elapsed = current_timestamp - last_claim_timestamp
        if time_elapsed < COOLDOWN_SECONDS:
            time_remaining_seconds = COOLDOWN_SECONDS - time_elapsed
            minutes_remaining = int(time_remaining_seconds // 60)
            seconds_remaining = int(time_remaining_seconds % 60)
            await update.message.reply_text(
                f"⏳ You need to wait a bit longer before your next claim. "
                f"Please try again in `{minutes_remaining}` minutes and `{seconds_remaining}` seconds."
            )
            return
        else:
            can_proceed = True
            action_type = "update"
    else:
        can_proceed = True
        action_type = "add"

    if not can_proceed:
        return

    await update.message.reply_text(
        f"Claiming Tokens to account `{recipient_account_id}`. Please wait..."
    )

    try:
        command = ["node", "sendhbar.js", recipient_account_id, str(CCC_AMOUNT)]

        process_result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False
        )

        if process_result.returncode == 0:
            db_success = False
            if action_type == "add":
                db_success = add_user_claim(telegram_user_id, recipient_account_id, current_timestamp)
            elif action_type == "update":
                db_success = update_user_claim_timestamp(telegram_user_id, current_timestamp)

            if db_success:
                response_message = (
                    f"🎉 Token transfer successful to `{recipient_account_id}`! "
                    f"Amount: {CCC_AMOUNT} CCC.\n\n"
                    f"Details:\n```\n{process_result.stdout}\n```"
                )
                await update.message.reply_text(response_message, parse_mode='Markdown')
            else:
                await update.message.reply_text(
                    f"🎉 Token transfer successful to `{recipient_account_id}`! "
                    "However, there was an issue saving your claim data to the database. "
                    "Please notify the bot administrator about this error."
                )
        else:
            error_message = (
                f"❌ Token transfer failed for `{recipient_account_id}`.\n\n"
                f"Error details:\n```\n{process_result.stderr}\n{process_result.stdout}\n```"
            )
            await update.message.reply_text(error_message, parse_mode='Markdown')

    except FileNotFoundError:
        await update.message.reply_text(
            "Error: 'node' command or 'sendhbar.js' script not found. "
            "Please ensure Node.js is installed and `sendhbar.js` is in the same directory "
            "as the bot script, or provide the full path to `sendhbar.js`."
        )
    except Exception as e:
        await update.message.reply_text(
            f"An unexpected error occurred: `{e}`"
        )
        print(f"Unhandled error in send_hbar_to_account: {e}")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Echoes the user's message if it's not a recognized command or a valid account ID.
    Provides guidance on how to use the bot.
    """
    await update.message.reply_text(
        "I'm not sure how to process that. Please use the `/start` command to open the miner."
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Logs errors caused by updates to the bot.
    Useful for debugging issues.
    """
    print(f"Update {update} caused error {context.error}")


def main() -> None:
    """
    The main function that sets up and starts the Telegram bot.
    It initializes the database and registers all command and message handlers.
    """
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env file. Please set it.")
        return

    if WEBAPP_URL == "YOUR_WEBAPP_URL_HERE":
        print(
            "Warning: WEBAPP_URL is not set. Please update your .env file or the script with the actual URL to your index.html.")

    # Initialize the database before starting the bot
    init_db()

    # Create the Application and pass your bot's token.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers for commands and messages
    application.add_handler(CommandHandler("start", start))

    # Handle WebApp data
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    # Keep the original MessageHandler for faucet if you want both functionalities
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, send_hbar_to_account))

    # Register the error handler
    application.add_error_handler(error_handler)

    print("Bot started! Send messages to your bot on Telegram.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()