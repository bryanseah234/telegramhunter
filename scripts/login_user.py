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
    print("This script will create a 'user_session.session' file.")
    print("You will need to enter your phone number and the OTP code sent to your Telegram app.")
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
    
    # Allow user to specify session name (default: user_session)
    session_input = input("Enter a name for this session (default: 'user_session'): ").strip()
    session_name = session_input if session_input else "user_session"
    
    # Store session name in a known file for the app to pick up if needed?
    # For now, just create the session file. The UserAgentService can be updated to read from env or config.
    
    session_file_path = os.path.join(base_dir, session_name)
    
    # Check for directory conflict
    if os.path.isdir(session_file_path + ".session"):
        print(f"\n❌ CRITICAL ERROR: '{session_name}.session' exists as a DIRECTORY!")
        print(f"👉 Docker has likely created this as a folder because the file didn't exist when mounted.")
        print(f"👉 ACTION REQUIRED: Delete the directory '{session_file_path}.session' and run this script again.")
        return
    
    print(f"📍 Session will be saved to: {session_file_path}.session")

    # Use /tmp to avoid SQLite locking issues on mounted volumes (WSL/Docker)
    import shutil
    temp_session_path = os.path.join("/tmp", session_name)
    
    # Clean up temp if exists
    if os.path.exists(temp_session_path + ".session"):
        os.remove(temp_session_path + ".session")

    print(f"📍 Session will be temporarily created at: {temp_session_path}.session")

    import sqlite3
    try:
        # Create client on temp path
        client = TelegramClient(temp_session_path, api_id, api_hash)
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        print(f"\n❌ Error initializing Telegram Client: {e}")
        return

    async def main():
        await client.start()
        print("\n✅ Login Successful!")
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
        
        # Disconnect to release lock
        await client.disconnect()
        
        # Copy to final destination
        final_path = session_file_path + ".session"
        print(f"📦 Moving session to: {final_path}")
        try:
            shutil.copy2(temp_session_path + ".session", final_path)
        except PermissionError:
            print(f"\n❌ Permission Error: Unable to write to {final_path}")
            print("💡 The container is running as a non-root user and cannot write to the mounted volume.")
            print("👉 Try running the script as root:")
            print("   docker-compose run --rm --user root api python scripts/login_user.py")
            return
        
        print(f"✅ Session saved successfully to: {final_path}")
        print("\nYou can now run the scraper with auto-invite enabled.")
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
        print(f"Session saved to: {os.path.abspath(session_file_path + '.session')}")
        print("\nYou can now run the scraper with auto-invite enabled.")

    try:
        client.loop.run_until_complete(main())
    except Exception as e:
        if "attempt to write a readonly database" in str(e) or "database is locked" in str(e):
            print(f"\n❌ DATABASE ERROR: {e}")
            print(f"⚠️  The session file '{session_name}.session' is likely locked by running containers or has permission issues.")
            print("👉 STEPS TO FIX:")
            print("   1. Stop all containers:  docker-compose down")
            print(f"   2. Delete the file:      rm {session_name}.session")
            print("   3. Run this script again.")
        else:
            raise e

if __name__ == "__main__":
    interactive_login()
