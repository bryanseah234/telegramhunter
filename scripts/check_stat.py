import asyncio
import os
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def check_pending():
    print("🔍 Checking pending messages in DB (via REST)...")
    
    # 1. Check total unbroadcasted
    try:
        # GET /rest/v1/exfiltrated_messages?is_broadcasted=eq.false&select=id.count()
        # Supabase syntax: select=count=exact&head=true to just get count
        url = f"{SUPABASE_URL}/rest/v1/exfiltrated_messages?is_broadcasted=eq.false&select=id"
        # We perform a HEAD request or GET with count header
        headers = {**HEADERS, "Prefer": "count=exact"}
        res = requests.get(url, headers=headers)
        
        # The count is in the 'Content-Range' header: '0-5/6' means 6 total.
        content_range = res.headers.get("Content-Range", "0-0/0")
        total_pending = content_range.split("/")[-1]
        
        print(f"📊 Total Pending (is_broadcasted=False): {total_pending}")
    except Exception as e:
        print(f"❌ Error checking count: {e}")

    # 2. Check claimed but not sent
    try:
        # broadcast_claimed_at is NOT NULL
        url = f"{SUPABASE_URL}/rest/v1/exfiltrated_messages?is_broadcasted=eq.false&broadcast_claimed_at=not.is.null&select=id,broadcast_claimed_at"
        res = requests.get(url, headers=HEADERS)
        data = res.json()
        
        print(f"⚠️ Currently Claimed (In Progress/Stuck): {len(data)}")
        if len(data) > 0:
            print("   Sample stuck IDs:", [x['id'] for x in data[:3]])

    except Exception as e:
        print(f"❌ Error checking stuck messages: {e}")

if __name__ == "__main__":
    check_pending()
