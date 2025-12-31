import asyncio
import sys
import os
import hashlib
import time

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db
from app.core.security import security
from app.services.scanners import ShodanService, FofaService, GithubService, CensysService, HybridAnalysisService

# Initialize Services
shodan = ShodanService()
fofa = FofaService()
github = GithubService()
censys = CensysService()
hybrid = HybridAnalysisService()

def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def save_manifest(results, source_name: str, verbose=True):
    saved_count = 0
    for item in results:
        token = item.get("token")
        if not token or token == "MANUAL_REVIEW_REQUIRED":
            continue
        
        token_hash = _calculate_hash(token)
        try:
            encrypted_token = security.encrypt(token)
            data = {
                "bot_token": encrypted_token,
                "token_hash": token_hash,
                "source": source_name,
                "status": "pending",
                "meta": item.get("meta", {})
            }
            
            # Upsert without .select() chain first
            res = db.table("discovered_credentials").upsert(data, on_conflict="token_hash", ignore_duplicates=True).execute()
            
            if res.data:
                print(f"  🎯 [NEW] Saved Credential ID: {res.data[0]['id']}")
                saved_count += 1
            else:
                # If ignore_duplicates=True and it existed, data might be empty.
                # We need to fetch the ID to be sure (though this script only counts new ones, 
                # strictly speaking we don't need the ID if we aren't using it immediately below).
                # But for correctness, let's just log it.
                # If you need the ID for enrichment downstream in this script (which we don't currently),
                # you would query it here.
                # saved_count += 0
                pass
        except Exception as e:
            if verbose: print(f"  ❌ Save Error: {e}")
            pass
    return saved_count

async def run_scanners():
    print("🚀 Starting LOCAL OSINT Scan (All Sources)...")
    print("-------------------------------------------------")

    # 1. Hybrid Analysis
    print("\n🦠 [HybridAnalysis] Starting Scan...")
    try:
        query = "api.telegram.org"
        print(f"  > Query: {query}")
        results = hybrid.search(query)
        # HA often returns manual review needed, but let's try
        count = save_manifest(results, "hybrid_analysis")
        print(f"  ✅ Processed {len(results)} reports ({count} tokens saved).")
    except Exception as e:
        print(f"  ❌ HybridAnalysis Error: {e}")

    # 2. Censys
    print("\n🔍 [Censys] Starting Scan...")
    try:
        # User requested simplified query + active verification
        query = "\"api.telegram.org\""
        print(f"  > Query: {query}")
        print("  > Note: Active verification enabled (scanning ports 80/443)")
        results = censys.search(query)
        count = save_manifest(results, "censys")
        print(f"  ✅ Saved {count} new credentials (from {len(results)} hits).")
    except Exception as e:
        print(f"  ❌ Censys Error: {e}")

    # 3. Shodan
    print("\n🌎 [Shodan] Starting Scan...")
    shodan_queries = [
        "http.html:\"api.telegram.org\"",
        "http.html:\"bot_token\"", 
        "http.title:\"Telegram Bot\"",
        "http.title:\"Telegram Login\""
    ]
    
    for q in shodan_queries:
        print(f"  > Querying: {q}")
        try:
            results = shodan.search(q)
            count = save_manifest(results, "shodan")
            print(f"    ✅ Saved {count} new credentials (from {len(results)} hits).")
            time.sleep(1)
        except Exception as e:
            print(f"    ❌ Error: {e}")

    # 4. GitHub
    print("\n🐱 [GitHub] Starting Scan...")
    dorks = [
        "filename:.env api.telegram.org",
        "path:config api.telegram.org",
        "\"TELEGRAM_BOT_TOKEN\"",
        "language:python \"ApplicationBuilder\" \"token\"",
        "language:python \"Telethon\" \"api_id\"",
        "filename:config.json \"bot_token\"",
        "filename:settings.py \"TELEGRAM_TOKEN\"",
         "\"api.telegram.org\""
    ]
    
    total_gh = 0
    for i, dork in enumerate(dorks):
        print(f"  > Dorking: {dork}")
        try:
            results = github.search(dork)
            count = save_manifest(results, "github")
            total_gh += count
            print(f"    Found {len(results)} matches, {count} new.")
        except Exception as e:
            print(f"    ❌ Error: {e}")
        
        if i < len(dorks) - 1:
            time.sleep(2) # Respect rate limits slightly

    print("\n-------------------------------------------------")
    print("🏁 Full Scan Complete.")
    print("   Check your Railway Worker logs (General Topic) for Enrichment alerts!")
    print("   (The worker will see the new 'pending' rows and enrich them automatically)")

if __name__ == "__main__":
    asyncio.run(run_scanners())
