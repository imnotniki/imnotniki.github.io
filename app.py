"""
CCC Mining Telegram Bot
"""
import json
import logging
from datetime import datetime, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CallbackContext, CommandHandler, MessageHandler, filters
from credentials import BOT_TOKEN, BOT_USERNAME, WEBAPP_URL

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
COOLDOWN_MINUTES = 100
COOLDOWN_SECONDS = COOLDOWN_MINUTES * 60

# Initialize database
from faucet import init_db
init_db()

# Import database functions
from faucet import get_mining_status, process_mining

async def start(update: Update, context: CallbackContext):
    """Send a message with a button that opens the WebApp."""
    # Check if the update has a message (for /start command)
    if update.message:
        user_id = update.effective_user.id

        # Get current mining status
        status = get_mining_status(user_id)

        # Create a keyboard with a button that opens the WebApp
        keyboard = [
            [KeyboardButton(
                text="🚀 Open Mining App",
                web_app=WebAppInfo(url=f"{WEBAPP_URL}?start_param={user_id}")
            )]
        ]

        # Create the reply markup with the keyboard
        reply_markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False
        )

        # Prepare welcome message with current status
        welcome_msg = """
🤖 *Welcome to CCC Mining Bot!* 🚀

Earn CCC tokens by mining every 100 minutes.

*Your current status:*
"""
        if status.get('is_mining'):
            mins = status.get('time_remaining', 0) // 60
            secs = status.get('time_remaining', 0) % 60
            welcome_msg += f"⏳ *Mining in progress*\nNext mining in: {mins}m {secs}s\n"
        else:
            welcome_msg += "✅ *Ready to mine!*\nTap the button below to start mining.\n"

        welcome_msg += f"\n💎 *Balance:* {status.get('balance', 0):.4f} CCC"

        # Send the message with the button
        await update.message.reply_text(
            welcome_msg,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        logger.warning("Received update without a message")

async def web_app_data(update: Update, context: CallbackContext):
    """Handle data received from the WebApp."""
    try:
        # Parse the data received from the WebApp
        data = json.loads(update.effective_message.web_app_data.data)
        user_id = update.effective_user.id

        logger.info(f"Received WebApp data from user {user_id}: {data}")

        # Handle different actions from the WebApp
        action = data.get('action')

        if action == 'get_status':
            # Get current mining status
            status = get_mining_status(user_id)
            await update.effective_message.reply_text(
                json.dumps(status)
            )

        elif action == 'start_mining':
            # Start mining process
            hedera_account = data.get('hedera_account', '')
            if not hedera_account:
                await update.effective_message.reply_text(
                    json.dumps({
                        'success': False,
                        'message': '❌ Error: No Hedera account provided'
                    })
                )
                return

            # Process the mining request
            result = process_mining(user_id, hedera_account)

            # Send the result back to the user
            await update.effective_message.reply_text(
                json.dumps(result)
            )

    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON: {e}")
        await update.effective_message.reply_text(
            json.dumps({
                'success': False,
                'message': '❌ Error: Invalid data format'
            })
        )
    except Exception as e:
        logger.error(f"Error in web_app_data: {e}")
        await update.effective_message.reply_text(
            json.dumps({
                'success': False,
                'message': f'❌ An error occurred: {str(e)}'
            })
        )

async def help_command(update: Update, context: CallbackContext):
    """Send a message when the command /help is issued."""
    help_text = (
        "🤖 *CCC Mining Bot*\n\n"
        "*Available commands:*\n"
        "/start - Start the bot and open the mining interface\n"
        "/help - Show this help message\n\n"
        "*How to mine CCC:*\n"
        "1. Click the 'Open Mining App' button\n"
        "2. Enter your Hedera account ID\n"
        "3. Click the MINE button to start mining\n"
        "4. Wait for the cooldown to finish\n"
        "5. Mine again when ready!\n\n"
        f"*Cooldown:* {COOLDOWN_MINUTES} minutes between mines"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

def main():
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # Handle messages that contain WebApp data
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    # Start the Bot
    logger.info(f"Bot started! Username: @{BOT_USERNAME}")
    application.run_polling()

if __name__ == '__main__':
    main()
