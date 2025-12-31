import asyncio
import sys
import os

# Ensure we can import app modules
# Add project root (one level up) to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.config import settings
from app.services.scanners import ShodanService, FofaService, GithubService, CensysService, HybridAnalysisService
from telegram import Bot
from supabase import create_client

async def verify_telegram_bot():
    print("\n--- Verifying Monitoring Bot ---")
    token = settings.MONITOR_BOT_TOKEN
    if not token or "your-bot-token" in token:
        print("❌ SKIPPED: MONITOR_BOT_TOKEN not set or default.")
        return

    try:
        bot = Bot(token=token)
        me = await bot.get_me()
        print(f"✅ SUCCESS: Connected as @{me.username} (ID: {me.id})")
    except Exception as e:
        print(f"❌ FAILED: {e}")

def verify_shodan():
    print("\n--- Verifying Shodan ---")
    if not settings.SHODAN_KEY:
        print("❌ SKIPPED: SHODAN_KEY not set.")
        return

    s = ShodanService()
    try:
        results = s.search("product:Telegram limit:1")
        print(f"ℹ️  Scan executed. Result count: {len(results)}")
        print("✅ SUCCESS: API responded (check above for any detailed errors)")
    except Exception as e:
        print(f"❌ FAILED: {e}")

def verify_fofa():
    print("\n--- Verifying FOFA ---")
    if not settings.FOFA_KEY:
        print("❌ SKIPPED: FOFA_KEY not set.")
        return

    s = FofaService()
    try:
        results = s.search('body="api.telegram.org"')
        print(f"ℹ️  Scan executed. Result count: {len(results)}")
        print("✅ SUCCESS: API responded")
    except Exception as e:
        print(f"❌ FAILED: {e}")

def verify_supabase():
    print("\n--- Verifying Supabase ---")
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY or "your-project" in settings.SUPABASE_URL:
        print("❌ SKIPPED: Supabase credentials not configured.")
        return

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        # Simple select to check connection
        res = client.table("discovered_credentials").select("*", count="exact").limit(1).execute()
        print(f"✅ SUCCESS: Connected. Found {res.count} records.")
    except Exception as e:
        print(f"❌ FAILED: {e}")

def verify_github_integration():
    print("\n--- Verifying GitHub ---")
    if not settings.GITHUB_TOKEN:
        print("❌ SKIPPED: GITHUB_TOKEN not set.")
        return
        
    gs = GithubService()
    try:
        # Search for something benign
        results = gs.search("filename:Dockerfile repo:bryanseah234/telegramhunter")
        if isinstance(results, list):
             print(f"✅ SUCCESS: API responded. Found {len(results)} matches.")
        else:
             print("❌ FAILED: API return format invalid.")
    except Exception as e:
        print(f"❌ FAILED: {e}")

def verify_censys():
    print("\n--- Verifying Censys ---")
    if not settings.CENSYS_ID:
        print("❌ SKIPPED: CENSYS_ID (Token) not set.")
        return
        
    cs = CensysService()
    try:
        # Simple query for verification
        results = cs.search("services.port: 80 limit:1")
        # should return list
        print("✅ SUCCESS: API responded (Check account usage for credits)")
    except Exception as e:
        print(f"❌ FAILED: {e}")

def verify_hybrid():
    print("\n--- Verifying Hybrid Analysis ---")
    if not settings.HYBRID_ANALYSIS_KEY:
        print("❌ SKIPPED: HYBRID_ANALYSIS_KEY not set.")
        return
        
    ha = HybridAnalysisService()
    try:
        # Switching to /key/current to verify the KEY itself.
        headers = {
            'api-key': settings.HYBRID_ANALYSIS_KEY,
            'User-Agent': 'Falcon Sandbox'
        }
        import requests
        res = requests.get("https://www.hybrid-analysis.com/api/v2/key/current", headers=headers, timeout=10)
        
        if res.status_code == 200:
             print("✅ SUCCESS: Hybrid Analysis Key verified.")
        else:
             print(f"❌ FAILED: API Key Check returned {res.status_code}")
             print(f"   Response: {res.text[:100]}")
    except Exception as e:
        print(f"❌ FAILED: {e}")


async def main():
    print("Locked and Loaded. Verifying Integrations...")
    print(f"Environment: {settings.ENV}")
    
    await verify_telegram_bot()
    verify_shodan()
    verify_fofa()
    verify_github_integration()
    verify_censys()
    verify_hybrid()
    verify_supabase()

if __name__ == "__main__":
    from dotenv import load_dotenv
    # Load env from parent directory
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
    
    # Force settings reload
    from app.core import config
    from importlib import reload
    reload(config)
    from app.core.config import settings
    
    asyncio.run(main())
