import os
import subprocess
import sqlite3
import time  # Used for managing timestamps for the cooldown
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Load environment variables from .env file
load_dotenv()

# Get your Telegram bot token from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Define the fixed amount of CCC to send
CCC_AMOUNT = 1  # Changed from HBAR_AMOUNT and set to 1

# --- Database Configuration ---
# The name of the SQLite database file
DB_FILE = 'faucet.db'
# Cooldown period in minutes
COOLDOWN_MINUTES = 100
# Cooldown period converted to seconds for easier calculation
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

        # Faucet claims table
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
                last_mine_timestamp REAL NOT NULL,
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

    Args:
        telegram_user_id (int): The unique ID of the Telegram user.

    Returns:
        tuple (str, float) or None: A tuple containing (hedera_account_id, last_claim_timestamp)
        if the user is found in the database, otherwise None.
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
    Inserts a new user record into the database.
    This function should only be called if the user does not exist in the database.

    Args:
        telegram_user_id (int): The unique ID of the Telegram user.
        hedera_account_id (str): The Hedera account ID associated with the user.
        timestamp (float): The Unix timestamp of the current claim.

    Returns:
        bool: True if the insertion was successful, False otherwise.
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
        # Check if a row was actually inserted (changes() returns 1 for insert)
        return cursor.rowcount == 1
    except sqlite3.Error as e:
        print(f"Error adding new user claim for {telegram_user_id}: {e}")
        return False


def update_user_claim_timestamp(telegram_user_id: int, timestamp: float):
    """
    Updates the last claim timestamp for an existing user.

    Args:
        telegram_user_id (int): The unique ID of the Telegram user.
        timestamp (float): The new Unix timestamp for the claim.

    Returns:
        bool: True if the update was successful, False otherwise.
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


def get_mining_status(telegram_user_id: int) -> dict:
    """
    Get the mining status for a user.

    Args:
        telegram_user_id (int): The Telegram user ID

    Returns:
        dict: Dictionary containing mining status and balance
    """
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
            # User not in mining table, initialize with 0 balance
            cursor.execute('''
                INSERT INTO ccc_mining (telegram_user_id, last_mine_timestamp, ccc_balance)
                VALUES (?, ?, 0.0)
            ''', (telegram_user_id, 0.0))
            conn.commit()
            return {
                'can_mine': True,
                'time_remaining': 0,
                'balance': 0.0,
                'is_mining': False
            }

        last_mine_time, balance = result
        time_since_last_mine = current_time - last_mine_time

        if last_mine_time == 0 or time_since_last_mine >= COOLDOWN_SECONDS:
            return {
                'can_mine': True,
                'time_remaining': 0,
                'balance': balance,
                'is_mining': False
            }
        else:
            return {
                'can_mine': False,
                'time_remaining': int(COOLDOWN_SECONDS - time_since_last_mine),
                'balance': balance,
                'is_mining': True
            }

    except sqlite3.Error as e:
        print(f"Error getting mining status: {e}")
        return {
            'can_mine': False,
            'time_remaining': 0,
            'balance': 0.0,
            'is_mining': False,
            'error': str(e)
        }
    finally:
        conn.close()


def process_mining(telegram_user_id: int, hedera_account_id: str) -> dict:
    """
    Process a mining request from a user.

    Args:
        telegram_user_id (int): The Telegram user ID
        hedera_account_id (str): The Hedera account ID

    Returns:
        dict: Result of the mining operation
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        current_time = time.time()

        # Check if user exists in faucet_claims
        cursor.execute('''
            SELECT 1 FROM faucet_claims WHERE telegram_user_id = ?
        ''', (telegram_user_id,))

        if not cursor.fetchone():
            # Add user to faucet_claims if not exists
            cursor.execute('''
                INSERT INTO faucet_claims (telegram_user_id, hedera_account_id, last_claim_timestamp)
                VALUES (?, ?, 0.0)
            ''', (telegram_user_id, hedera_account_id))

        # Get current mining status
        status = get_mining_status(telegram_user_id)

        if not status['can_mine']:
            return {
                'success': False,
                'message': f'Please wait {status["time_remaining"] // 60} minutes before mining again.',
                'status': status
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
            'next_mine_time': current_time + COOLDOWN_SECONDS
        }

    except sqlite3.Error as e:
        print(f"Error processing mining: {e}")
        return {
            'success': False,
            'message': f'Database error: {str(e)}'
        }
    finally:
        conn.close()


# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends a welcome message to the user when the /start command is issued.
    Includes information about the bot's functionality and new rules.
    """
    await update.message.reply_text(
        "Hello! I'm your CCC transfer bot. "
        "Send me a Hedera Account ID (e.g., `0.0.123456`) and I'll send 1 CCC Token to it.\n\n"
        "**Important Rules:**\n"
        "• Each Telegram user can only claim CCC for one Hedera account ID.\n"
        f"• There is a **{COOLDOWN_MINUTES} minute cooldown** between claims.\n\n"
        "To send CCC, simply type the account ID. Example: `0.0.123456`"
    )


async def send_hbar_to_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles incoming messages, validates the Hedera account ID, checks cooldown,
    and attempts to execute the Token transfer script.
    It also manages user data in the SQLite database.
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

    # Retrieve user data from the database
    user_data = get_user_data(telegram_user_id)

    can_proceed = False

    if user_data:
        # If user data exists, unpack it
        stored_hedera_account_id, last_claim_timestamp = user_data

        # Check if the user is trying to claim for a different Hedera account than their registered one
        if stored_hedera_account_id != recipient_account_id:
            await update.message.reply_text(
                "🚫 You have already claimed CCC for a different Hedera Account ID "
                f"(`{stored_hedera_account_id}`). "
                "Each Telegram user can only claim for one Hedera account. "
                "If you believe this is an error or need to change your linked account, "
                "please contact the bot administrator."
            )
            return

        # Check the cooldown period
        time_elapsed = current_timestamp - last_claim_timestamp
        if time_elapsed < COOLDOWN_SECONDS:
            time_remaining_seconds = COOLDOWN_SECONDS - time_elapsed
            # Calculate remaining time in minutes and seconds for a user-friendly message
            minutes_remaining = int(time_remaining_seconds // 60)
            seconds_remaining = int(time_remaining_seconds % 60)
            await update.message.reply_text(
                f"⏳ You need to wait a bit longer before your next claim. "
                f"Please try again in `{minutes_remaining}` minutes and `{seconds_remaining}` seconds."
            )
            return
        else:
            can_proceed = True  # Cooldown passed
            action_type = "update"  # Indicate that we need to update the timestamp later
    else:
        # New user, no cooldown check needed
        can_proceed = True
        action_type = "add"  # Indicate that we need to add a new entry later

    if not can_proceed:
        # This should ideally not be reached due to the `return` statements above,
        # but as a safeguard.
        return

    # If all checks pass, proceed with transfer attempt
    await update.message.reply_text(
        f"Claiming Tokens to account `{recipient_account_id}`. Please wait..."
    )

    try:
        # Construct the command to run your Node.js script.
        # Ensure 'node' is in your system's PATH and 'sendhbar.js' is in the same directory,
        # or provide the full path to sendhbar.js.
        command = ["node", "sendhbar.js", recipient_account_id, str(CCC_AMOUNT)]  # Used CCC_AMOUNT

        # Execute the command as a new process.
        # capture_output=True means stdout and stderr will be captured.
        # text=True decodes stdout/stderr as text.
        # check=False means it will not raise CalledProcessError for non-zero exit codes,
        # allowing us to handle success/failure based on process_result.returncode.
        process_result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False
        )

        if process_result.returncode == 0:
            # Command executed successfully, now update the database based on action_type
            db_success = False
            if action_type == "add":
                db_success = add_user_claim(telegram_user_id, recipient_account_id, current_timestamp)
            elif action_type == "update":
                db_success = update_user_claim_timestamp(telegram_user_id, current_timestamp)

            if db_success:
                response_message = (
                    f"🎉 Token transfer successful to `{recipient_account_id}`! "
                    f"Amount: {CCC_AMOUNT} CCC.\n\n"  # Updated message to reflect CCC
                    f"Details:\n```\n{process_result.stdout}\n```"
                )
                await update.message.reply_text(response_message, parse_mode='Markdown')
            else:
                # This case indicates the Token transfer succeeded but DB update/insert failed
                await update.message.reply_text(
                    f"🎉 Token transfer successful to `{recipient_account_id}`! "
                    "However, there was an issue saving your claim data to the database. "
                    "Please notify the bot administrator about this error."
                )
        else:
            # Command failed
            error_message = (
                f"❌ Token transfer failed for `{recipient_account_id}`.\n\n"
                f"Error details:\n```\n{process_result.stderr}\n{process_result.stdout}\n```"
            # Include stdout for more debugging info
            )
            await update.message.reply_text(error_message, parse_mode='Markdown')

    except FileNotFoundError:
        # This error occurs if 'node' command or 'sendhbar.js' script cannot be found
        await update.message.reply_text(
            "Error: 'node' command or 'sendhbar.js' script not found. "
            "Please ensure Node.js is installed and `sendhbar.js` is in the same directory "
            "as the bot script, or provide the full path to `sendhbar.js`."
        )
    except Exception as e:
        # Catch any other unexpected errors during the subprocess execution
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
        "I'm not sure how to process that. Please send a Hedera Account ID "
        "(e.g., `0.0.123456`) or use the `/start` command."
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

    # Initialize the database before starting the bot
    init_db()

    # Create the Application and pass your bot's token.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers for commands and messages
    application.add_handler(CommandHandler("start", start))

    # MessageHandler to process text messages that are NOT commands.
    # This ensures that only plain text (expected to be Hedera account IDs)
    # is passed to send_hbar_to_account.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, send_hbar_to_account))

    # Register the error handler
    application.add_error_handler(error_handler)

    # Start the bot and keep it running until interrupted (e.g., by Ctrl-C)
    print("Bot started! Send messages to your bot on Telegram.")
    # run_polling listens for updates from Telegram servers
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()