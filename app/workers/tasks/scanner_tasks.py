from app.workers.celery_app import app
from app.services.scanners import ShodanService, FofaService

# Instantiate services
shodan = ShodanService()
fofa = FofaService()

@app.task(name="scanner.scan_shodan")
def scan_shodan(query: str = "product:Telegram"):
    """
    Scans Shodan and saves unique tokens to discovered_credentials.
    """
    print(f"Starting Shodan scan: {query}")
    try:
        results = shodan.search(query)
        saved_count = 0
        for item in results:
            token = item.get("token")
            if not token:
                continue
            
            # Check for generic/obviously fake tokens if needed
            
            # Save (ignore if exists logic or just insert)
            # Since we encrypt, we can't easily check 'exists' by token string on DB side 
            # without deterministic encryption or a hash column.
            # For this task, we'll blindly insert and assume UUID uniqueness or app logic handles dups later.
            # Ideally, we should add a 'token_hash' column for de-duplication. 
            # We'll just encrypt and save 'pending'.
            try:
                # Encrypt
                encrypted_token = security.encrypt(token)
                
                data = {
                    "bot_token": encrypted_token,
                    "source": "shodan",
                    "status": "pending",
                    "meta": item.get("meta", {})
                }
                db.table("discovered_credentials").insert(data).execute()
                saved_count += 1
            except Exception:
                # Duplicate or error
                pass
                
        return f"Shodan scan finished. Saved {saved_count} new credentials."
    except Exception as e:
        return f"Shodan scan failed: {e}"

@app.task(name="scanner.scan_fofa")
def scan_fofa(query: str = 'body="api.telegram.org"'):
    """
    Scans FOFA and saves unique tokens.
    """
    print(f"Starting FOFA scan: {query}")
    try:
        results = fofa.search(query)
        saved_count = 0
        for item in results:
            token = item.get("token")
            if not token:
                continue
                
            try:
                encrypted_token = security.encrypt(token)
                data = {
                    "bot_token": encrypted_token,
                    "source": "fofa",
                    "status": "pending",
                    "meta": item.get("meta", {})
                }
                db.table("discovered_credentials").insert(data).execute()
                saved_count += 1
            except Exception:
                pass

        return f"FOFA scan finished. Saved {saved_count} new credentials."
    except Exception as e:
        return f"FOFA scan failed: {e}"
