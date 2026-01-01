import sys
import os
import asyncio
from telethon import TelegramClient

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.config import settings

def interactive_login():
    print("🔐 Telegram User Login")
    print("----------------------")
    print("This script will create a 'user_session.session' file.")
    print("You will needs to enter your phone number and the OTP code sent to your Telegram app.")
    print("----------------------")

    api_id = settings.TELEGRAM_API_ID
    api_hash = settings.TELEGRAM_API_HASH
    
    if not api_id or not api_hash:
        print("❌ Error: TELEGRAM_API_ID or TELEGRAM_API_HASH missing in .env")
        return

    session_file = "user_session" # Telethon adds .session automatically

    client = TelegramClient(session_file, api_id, api_hash)

    async def main():
        await client.start()
        print("\n✅ Login Successful!")
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
        print(f"Session saved to: {os.path.abspath(session_file + '.session')}")
        print("\nYou can now run the scraper with auto-invite enabled.")

    client.loop.run_until_complete(main())

if __name__ == "__main__":
    interactive_login()
