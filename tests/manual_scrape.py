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
            
            # Upsert
            res = db.table("discovered_credentials").upsert(data, on_conflict="token_hash", ignore_duplicates=True).select("id").execute()
            
            if res.data:
                print(f"  🎯 [NEW] Saved Credential ID: {res.data[0]['id']}")
                saved_count += 1
        except Exception as e:
            if verbose: print(f"  ❌ Save Error: {e}")
            pass
    return saved_count

async def run_scanners():
    print("🚀 Starting LOCAL OSINT Scan (All Sources)...")
    print("-------------------------------------------------")

    # 1. GitHub
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

    # 2. Shodan
    print("\n🌎 [Shodan] Starting Scan...")
    try:
        query = "product:Telegram"
        print(f"  > Query: {query}")
        results = shodan.search(query)
        count = save_manifest(results, "shodan")
        print(f"  ✅ Saved {count} new credentials (from {len(results)} hits).")
    except Exception as e:
        print(f"  ❌ Shodan Error: {e}")

    # 3. Censys
    print("\n🔍 [Censys] Starting Scan...")
    try:
        query = "services.port: 443 and services.http.response.body: \"api.telegram.org\""
        print(f"  > Query: {query}")
        results = censys.search(query)
        count = save_manifest(results, "censys")
        print(f"  ✅ Saved {count} new credentials (from {len(results)} hits).")
    except Exception as e:
        print(f"  ❌ Censys Error: {e}")

    # 4. FOFA
    print("\n🦈 [FOFA] Starting Scan...")
    try:
        query = 'body="api.telegram.org"'
        print(f"  > Query: {query}")
        results = fofa.search(query)
        count = save_manifest(results, "fofa")
        print(f"  ✅ Saved {count} new credentials (from {len(results)} hits).")
    except Exception as e:
        print(f"  ❌ FOFA Error: {e}")

    # 5. Hybrid Analysis
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

    print("\n-------------------------------------------------")
    print("🏁 Full Scan Complete.")
    print("   Check your Railway Worker logs (General Topic) for Enrichment alerts!")
    print("   (The worker will see the new 'pending' rows and enrich them automatically)")

if __name__ == "__main__":
    asyncio.run(run_scanners())
