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

    # Force session file to project root
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Telethon adds .session automatically, so we provide path without extension for client init
    # BUT we want to ensure it lands in base_dir.
    session_name = "user_session"
    session_file_path = os.path.join(base_dir, session_name)
    
    print(f"📍 Session will be saved to: {session_file_path}.session")

    import sqlite3
    try:
        client = TelegramClient(session_file_path, api_id, api_hash)
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        print(f"\n❌ Error initializing Telegram Client: {e}")
        print(f"⚠️  The session file '{session_name}.session' might be corrupted or locked by another process.")
        print("👉 Try running 'docker-compose down' to stop all other containers.")
        print(f"👉 If that fails, delete the file '{session_file_path}.session' and try again.")
        return

    async def main():
        await client.start()
        print("\n✅ Login Successful!")
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
        print(f"Session saved to: {os.path.abspath(session_file_path + '.session')}")
        print("\nYou can now run the scraper with auto-invite enabled.")

    client.loop.run_until_complete(main())

if __name__ == "__main__":
    interactive_login()
