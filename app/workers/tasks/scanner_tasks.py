from app.workers.celery_app import app
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

def _save_credentials(results, source_name: str):
    """Helper to save credentials with deduplication via Hash."""
    saved_count = 0
    for item in results:
        token = item.get("token")
        if not token or token == "MANUAL_REVIEW_REQUIRED":
            # For HA manual review, we might want to log it differently or skip.
            # Currently skipping manual review placeholders to keep DB clean of non-tokens.
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
            
            db.table("discovered_credentials").upsert(data, on_conflict="token_hash", ignore_duplicates=True).execute()
            saved_count += 1
        except Exception as e:
            pass
    return saved_count

@app.task(name="scanner.scan_shodan")
def scan_shodan(query: str = "product:Telegram"):
    print(f"Starting Shodan scan: {query}")
    try:
        results = shodan.search(query)
        saved = _save_credentials(results, "shodan")
        return f"Shodan scan finished. Saved {saved} new credentials."
    except Exception as e:
        return f"Shodan scan failed: {e}"

@app.task(name="scanner.scan_fofa")
def scan_fofa(query: str = 'body="api.telegram.org"'):
    print(f"Starting FOFA scan: {query}")
    try:
        results = fofa.search(query)
        saved = _save_credentials(results, "fofa")
        return f"FOFA scan finished. Saved {saved} new credentials."
    except Exception as e:
        return f"FOFA scan failed: {e}"

@app.task(name="scanner.scan_github")
def scan_github(query: str = None):
    import time
    default_dorks = [
        "filename:.env api.telegram.org",
        "path:config api.telegram.org",
        "\"TELEGRAM_BOT_TOKEN\"",
        "language:python \"ApplicationBuilder\" \"token\"",
        "language:python \"Telethon\" \"api_id\""
    ]
    
    queries = [query] if query else default_dorks
    total_saved = 0
    errors = []

    print(f"Starting GitHub scan with {len(queries)} queries...")

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
        
        if len(queries) > 1:
            time.sleep(5) 

    result_msg = f"GitHub scan finished. Saved {total_saved} unique credentials."
    if errors:
        result_msg += f" (Encountered {len(errors)} errors)"
    return result_msg

@app.task(name="scanner.scan_censys")
def scan_censys(query: str = "services.port: 443 and services.http.response.body: \"api.telegram.org\""):
    print(f"Starting Censys scan: {query}")
    try:
        results = censys.search(query)
        saved = _save_credentials(results, "censys")
        return f"Censys scan finished. Saved {saved} new credentials."
    except Exception as e:
        return f"Censys scan failed: {e}"

@app.task(name="scanner.scan_hybrid")
def scan_hybrid(query: str = "api.telegram.org"):
    """
    Scans Hybrid Analysis for malware reports containing the query.
    Note: Token extraction is difficult without downloading samples.
    """
    print(f"Starting HybridAnalysis scan: {query}")
    try:
        results = hybrid.search(query)
        # Note: Current logic skips saving if token is "MANUAL_REVIEW_REQUIRED"
        # This is strictly for demonstration of integration. 
        # Real extraction requires downloading the report JSON details.
        saved = _save_credentials(results, "hybrid_analysis")
        return f"HybridAnalysis scan finished. (Logged {len(results)} reports - check logic for extraction)"
    except Exception as e:
        return f"HybridAnalysis scan failed: {e}"
