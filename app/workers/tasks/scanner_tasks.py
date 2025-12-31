from app.workers.celery_app import app
import asyncio # Ensure asyncio is imported
from app.services.broadcaster_srv import broadcaster_service
from app.services.scanners import ShodanService, FofaService, GithubService, CensysService, HybridAnalysisService
from app.core.security import security
from app.core.database import db
import hashlib

# Instantiate services
shodan = ShodanService()
fofa = FofaService()
github = GithubService()
censys = CensysService()
hybrid = HybridAnalysisService()

def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

async def _save_credentials_async(results, source_name: str):
    """Async helper to save credentials with deduplication via Hash."""
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
                new_id = res.data[0]['id']
                from app.workers.tasks.flow_tasks import enrich_credential
                
                await broadcaster_service.send_log(f"🎯 [{source_name}] New Credential Found! ID: `{new_id}`")
                
                enrich_credential.delay(new_id)
                saved_count += 1
        except Exception as e:
            pass
    return saved_count

def _save_credentials(results, source_name: str):
    """Sync wrapper for async save logic"""
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_save_credentials_async(results, source_name))

def _send_log_sync(message: str):
    """Sync wrapper to send logs via broadcaster."""
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(broadcaster_service.send_log(message))

@app.task(name="scanner.scan_shodan")
def scan_shodan(query: str = None):
    import time
    default_queries = [
        "http.html:\"api.telegram.org\"",
        "http.html:\"bot_token\"", 
        "http.title:\"Telegram Bot\"",
        "http.title:\"Telegram Login\"",
        "product:\"Telegram\""
    ]
    
    queries = [query] if query else default_queries
    total_saved = 0
    errors = []

    print(f"Starting Shodan scan with {len(queries)} queries...")
    _send_log_sync(f"🌎 [Shodan] Starting scan with {len(queries)} queries...")

    for q in queries:
        try:
            results = shodan.search(q)
            saved = _save_credentials(results, "shodan")
            total_saved += saved
            time.sleep(1) # Rate limit respect
        except Exception as e:
            errors.append(str(e))
            
    result_msg = f"Shodan scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        _send_log_sync(f"❌ [Shodan] Completed with errors: {errors[0]}...")

    _send_log_sync(f"🏁 [Shodan] Finished. Saved {total_saved} new credentials.")
    return result_msg

@app.task(name="scanner.scan_fofa")
def scan_fofa(query: str = 'body="api.telegram.org"'):
    print(f"Starting FOFA scan: {query}")
    _send_log_sync(f"🦈 [FOFA] Starting scan with query: `{query}`")
    try:
        results = fofa.search(query)
        saved = _save_credentials(results, "fofa")
        msg = f"FOFA scan finished. Saved {saved} new credentials."
        _send_log_sync(f"🏁 [FOFA] Finished. Saved {saved} new credentials.")
        return msg
    except Exception as e:
        _send_log_sync(f"❌ [FOFA] Scan failed: {e}")
        return f"FOFA scan failed: {e}"

@app.task(name="scanner.scan_github")
def scan_github(query: str = None):
    import time
    default_dorks = [
        "filename:.env api.telegram.org",
        "path:config api.telegram.org",
        "\"TELEGRAM_BOT_TOKEN\"",
        "language:python \"ApplicationBuilder\" \"token\"",
        "language:python \"Telethon\" \"api_id\"",
        "filename:config.json \"bot_token\"",
        "filename:settings.py \"TELEGRAM_TOKEN\"",
        "\"api.telegram.org\""  # Catch-all for any file containing the API URL
    ]
    
    queries = [query] if query else default_dorks
    total_saved = 0
    errors = []

    print(f"Starting GitHub scan with {len(queries)} queries...")
    _send_log_sync(f"🐱 [GitHub] Starting scan with {len(queries)} dorks...")

    for q in queries:
        print(f"Executing GitHub Dork: {q}")
        try:
            results = github.search(q)
            saved = _save_credentials(results, "github")
            total_saved += saved
            print(f"  > Found {len(results)} matches, saved {saved} new.")
        except Exception as e:
            print(f"  > Error: {e}")
            errors.append(str(e))
        
            time.sleep(5) 

    result_msg = f"GitHub scan finished. Saved {total_saved} unique credentials."
    if errors:
        result_msg += f" (Encountered {len(errors)} errors)"
    _send_log_sync(f"🏁 [GitHub] Finished. Saved {total_saved} unique credentials.")
    return result_msg

@app.task(name="scanner.scan_censys")
def scan_censys(query: str = "services.port: 443 and services.http.response.body: \"api.telegram.org\""):
    print(f"Starting Censys scan: {query}")
    _send_log_sync(f"🔍 [Censys] Starting scan with query: `{query}`")
    try:
        results = censys.search(query)
        saved = _save_credentials(results, "censys")
        msg = f"Censys scan finished. Saved {saved} new credentials."
        _send_log_sync(f"🏁 [Censys] Finished. Saved {saved} new credentials.")
        return msg
    except Exception as e:
        _send_log_sync(f"❌ [Censys] Failed: {e}")
        return f"Censys scan failed: {e}"

@app.task(name="scanner.scan_hybrid")
def scan_hybrid(query: str = "api.telegram.org"):
    """
    Scans Hybrid Analysis for malware reports containing the query.
    Note: Token extraction is difficult without downloading samples.
    """
    print(f"Starting HybridAnalysis scan: {query}")
    _send_log_sync(f"🦠 [HybridAnalysis] Starting scan for malware reports: `{query}`")
    try:
        results = hybrid.search(query)
        # Note: Current logic skips saving if token is "MANUAL_REVIEW_REQUIRED"
        # This is strictly for demonstration of integration. 
        # Real extraction requires downloading the report JSON details.
        saved = _save_credentials(results, "hybrid_analysis")
        msg = f"HybridAnalysis scan finished. (Logged {len(results)} reports)"
        _send_log_sync(f"🏁 [HybridAnalysis] Finished. Processed {len(results)} reports.")
        return msg
    except Exception as e:
        _send_log_sync(f"❌ [HybridAnalysis] Failed: {e}")
        return f"HybridAnalysis scan failed: {e}"
