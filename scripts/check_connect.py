import redis
from supabase import create_client
import os
import sys

# Load from .env manually to avoid app overhead
from dotenv import load_dotenv
load_dotenv()

REDIS_URL = os.getenv("REDIS_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

print(f"🕵️  Checking Connectivity...")
print(f"   Redis: {REDIS_URL}")
print(f"   Supabase: {SUPABASE_URL}")

def check_redis():
    print("\n[1/2] Testing Redis Connection (Timeout: 5s)...")
    try:
        r = redis.from_url(REDIS_URL, socket_timeout=5, socket_connect_timeout=5)
        if r.ping():
            print("   ✅ Redis Alive!")
    except Exception as e:
        print(f"   ❌ Redis Failed: {e}")

def check_supabase():
    print("\n[2/2] Testing Supabase Connection...")
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        # Simple query
        res = supabase.table("discovered_credentials").select("count", count="exact").limit(1).execute()
        print(f"   ✅ Supabase Alive! (Count: {res.count})")
    except Exception as e:
        print(f"   ❌ Supabase Failed: {e}")

if __name__ == "__main__":
    check_redis()
    check_supabase()
