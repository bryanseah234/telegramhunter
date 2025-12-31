
import asyncio
import sys
import os
from dotenv import load_dotenv

# Load env variables
load_dotenv()
sys.path.append(os.getcwd())

from app.core.config import settings
from supabase import create_client

def test_rw():
    print("--- Testing Supabase Read/Write ---")
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        print("❌ Supabase config missing.")
        return

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        
        # 1. Insert Dummy
        dummy_data = {
            "bot_token": "TEST_TOKEN_ENCRYPTED_PLACEHOLDER",
            "token_hash": "TEST_HASH_12345",
            "source": "TEST_SCRIPT",
            "status": "pending"
        }
        print("1. Attempting INSERT...")
        data = client.table("discovered_credentials").insert(dummy_data).execute()
        new_id = data.data[0]['id']
        print(f"   ✅ Inserted ID: {new_id}")
        
        # 2. Read it back
        print("2. Attempting SELECT...")
        res = client.table("discovered_credentials").select("*").eq("id", new_id).execute()
        if len(res.data) > 0:
             print(f"   ✅ Record found: {res.data[0]['token_hash']}")
        else:
             print("   ❌ Record NOT found after insert!")
             
        # 3. Delete it
        print("3. Attempting DELETE...")
        client.table("discovered_credentials").delete().eq("id", new_id).execute()
        print("   ✅ Record deleted.")
        
        print("\n🎉 Supabase Read/Write Fully Verified!")

    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        # Check for common RLS policies issues
        if "policy" in str(e).lower():
            print("   👉 Hint: Check your Supabase RLS policies. The Service Key should bypass them, but if using Anon key you might need policies.")

if __name__ == "__main__":
    test_rw()
