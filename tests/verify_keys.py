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
        # Use httpx directly to avoid proxy issues with newer versions
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    username = data["result"].get("username", "Unknown")
                    bot_id = data["result"].get("id", "Unknown")
                    print(f"✅ SUCCESS: Connected as @{username} (ID: {bot_id})")
                else:
                    print(f"❌ FAILED: {data.get('description', 'Unknown error')}")
            else:
                print(f"❌ FAILED: HTTP {resp.status_code}")
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
        # Use httpx directly to avoid library proxy issues
        import httpx
        headers = {
            "apikey": settings.SUPABASE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_KEY}"
        }
        resp = httpx.get(
            f"{settings.SUPABASE_URL}/rest/v1/discovered_credentials?select=id&limit=1",
            headers=headers,
            timeout=10
        )
        if resp.status_code == 200:
            print(f"✅ SUCCESS: Connected. Database accessible.")
        else:
            print(f"❌ FAILED: HTTP {resp.status_code} - {resp.text[:100]}")
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
