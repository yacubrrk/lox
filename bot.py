import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Аська лох")


def get_bot_token() -> str:
    token_keys = ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TOKEN")
    for key in token_keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    raise RuntimeError(
        "Set one of environment variables: BOT_TOKEN, TELEGRAM_BOT_TOKEN, TOKEN"
    )


def main() -> None:
    token = get_bot_token()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()


if __name__ == "__main__":
    main()
