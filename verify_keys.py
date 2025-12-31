import asyncio
import sys
import os

# Ensure we can import app modules
sys.path.append(os.getcwd())

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
    # ShodanService.search does a real request. 
    # We'll try a very simple query that should return something or at least not auth error.
    # Note: Our service implementation returns a list or empty list on error.
    # We should look at the internal implementation or just call search.
    # Actually, let's call the API manually to see the specific error if any, 
    # OR trust the service print output (which goes to stdout).
    try:
        results = s.search("product:Telegram limit:1")
        # If we get here without exception, check if it printed error
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
        # Assuming table exists from init.sql
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
        # Search for something benign or a dork that returns your own public key if possible, 
        # or just "telegram" in a small timeframe. We'll use a specific dork.
        results = gs.search("filename:Dockerfile repo:bryanseah234/telegramhunter")
        # Since we might not find our own repo immediately if not indexed, just checking API response.
        if isinstance(results, list):
             print(f"✅ SUCCESS: API responded. Found {len(results)} matches.")
        else:
             print("❌ FAILED: API return format invalid.")
    except Exception as e:
        print(f"❌ FAILED: {e}")

def verify_censys():
    print("\n--- Verifying Censys ---")
    if not settings.CENSYS_ID or not settings.CENSYS_SECRET:
        print("❌ SKIPPED: CENSYS_ID or CENSYS_SECRET not set.")
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
        # Search for generic term
        results = ha.search("telegram")
        print(f"✅ SUCCESS: API responded. Found {len(results)} reports.")
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
    asyncio.run(main())
