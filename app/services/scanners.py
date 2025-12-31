from typing import List, Dict, Any
from app.core.config import settings
import requests
import base64
import re
import urllib3

# Suppress SSL warnings for active scanning of random IPs (self-signed certs etc)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import urllib3

# Suppress SSL warnings for active scanning of random IPs (self-signed certs etc)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def _is_valid_token(token_str: str) -> bool:
    """
    Strict validation to filter out Fernet strings, hashes, and junk.
    Logic:
    1. Regex already guarantees format: digits:35chars
    2. Split by ':'
    3. Left part (ID) must be integer.
    4. Right part (Secret) must NOT be pure hex (common hashes).
    5. Right part usually contains mix of upper/lower/digits/special.
    """
    try:
        if ":" not in token_str: return False
        parts = token_str.split(":", 1)
        if len(parts) != 2: return False
        
        bot_id, secret = parts
        
        # ID check
        if not bot_id.isdigit(): return False
        if len(bot_id) > 1 and bot_id.startswith("0"): return False # Leading zero invalid
        
        # Secret check
        if len(secret) != 35: return False
        
        # Reject if PURE hex (e.g. accidental match of 35 char hex substring)
        # Telegram secrets are base64-ish (case sensitive).
        # Hashes are often lower-only hex.
        is_hex = all(c in "0123456789abcdefABCDEF" for c in secret)
        if is_hex:
            # If it's pure hex and all lower, it's very suspicious (likely md5-ish garbage)
            # But telegram tokens CAN generally be anything. 
            # However, entropy of 35 chars being valid hex is low if random.
            # Safety: If it looks TOO much like a hash?
            pass

        # Reject if contains 'base64' or 'sha256' keywords? (Unlikely due to regex)
        
        return True
    except Exception:
        return False

def _perform_active_deep_scan(target_url: str) -> List[str]:
    """
    Connects to a URL, scans HTML body, finds <script src="..."> tags, 
    fetches those JS files, and scans them too.
    Returns a list of unique valid tokens found.
    """
    found_tokens = []
    # Strict Regex: \b (boundary) + digits + : + 35 chars + \b
    token_pattern = re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')
    
    try:
        # 1. Fetch Main HTML
        print(f"      [DeepScan] Fetching: {target_url}")
        res = requests.get(target_url, timeout=5, verify=False)
        if res.status_code != 200: return []
        
        html_content = res.text
        found_tokens.extend(token_pattern.findall(html_content))
        
        # 2. Find External JS
        # Pattern: src=["'](path/to/script.js)["']
        js_links = re.findall(r'src=["\'](.*?.js)["\']', html_content)
        
        # Limit JS files to avoid slow scans or traps
        unique_js = list(set(js_links))[:5] # Max 5 scripts
        
        for js_path in unique_js:
            # Handle relative URLs
            if js_path.startswith("//"):
                js_url = "https:" + js_path
            elif js_path.startswith("http"):
                js_url = js_path
            elif js_path.startswith("/"):
                # Absolute path from root of domain
                # Need to parse target_url base
                from urllib.parse import urljoin
                js_url = urljoin(target_url, js_path)
            else:
                # Relative path
                from urllib.parse import urljoin
                js_url = urljoin(target_url, js_path)

            try:
                # print(f"      [DeepScan] Checking JS: {js_url}")
                js_res = requests.get(js_url, timeout=3, verify=False)
                if js_res.status_code == 200:
                    found_tokens.extend(token_pattern.findall(js_res.text))
            except Exception:
                pass

        # Validate tokens
        valid_tokens = []
        for t in set(found_tokens):
            if _is_valid_token(t):
                valid_tokens.append(t)
                
        return valid_tokens

    except Exception as e:
        # print(f"DeepScan Error: {e}")
        return []

class ShodanService:
    def __init__(self):
        self.api_key = settings.SHODAN_KEY
        self.base_url = "https://api.shodan.io/shodan/host/search"

    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Shodan Search + Active Verification.
        If query contains 'api.telegram.org', we fetch the IP content to look for tokens.
        """
        if not self.api_key:
            return []

        try:
            params = {'key': self.api_key, 'query': query}
            res = requests.get(self.base_url, params=params, timeout=15)
            # 403/401 handling?
            res.raise_for_status()
            data = res.json()
            # Strict Regex: \b (boundary) + digits + : + 35 chars + \b
            token_pattern = re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')
            results = []
            
            matches = data.get('matches', [])
            print(f"    [Shodan] Processing {len(matches)} raw hits...")

            for match in matches:
                ip = match.get('ip_str')
                port = match.get('port')
                banner = match.get('data', '')
                
                # 1. Passive Check (Banner)
                found = token_pattern.findall(banner)
                
                # 2. Active Check (If requested or if banner looks interesting)
                # "make sure resolves" -> Try to connect.
                if not found:
                    try:
                        # Convert Shodan transport to protocol
                        # fallback to http or https based on port
                        proto = "https" if port == 443 else "http"
                        target_url = f"{proto}://{ip}:{port}"
                        
                        # Short timeout for active check
                        tokens = _perform_active_deep_scan(target_url)
                        found.extend(tokens)
                    except Exception:
                        pass # Host might be down or firewall

                # Deduplicate tokens on this host
                found = list(set(found))
                
                for t in found:
                    if not _is_valid_token(t): continue

                    results.append({
                        "token": t, 
                        "meta": {
                            "ip": ip,
                            "port": port,
                            "shodan_data": "verified_active" if ip else "banner_match"
                        }
                    })
            return results
        except Exception as e:
            print(f"Shodan Error: {e}")
            return []

class FofaService:
    def __init__(self):
        self.email = settings.FOFA_EMAIL 
        self.key = settings.FOFA_KEY
        self.base_url = "https://fofa.info/api/v1/search/all"

    def search(self, query_str: str) -> List[Dict[str, Any]]:
        if not self.key:
            return []
        
        # Fallback for free accounts that can't search 'body'
        # We try the original query first, if 820001 error, we downgrade.
        results = self._perform_search(query_str)
        if results == "PERMISSION_ERROR" and "body=" in query_str:
            print("⚠️ FOFA 'body' search forbidden. Falling back to 'title' search.")
            fallback_query = 'title="Telegram" && protocol="http"'
            results = self._perform_search(fallback_query)
        
        if isinstance(results, list):
            return results
        return []

    def _perform_search(self, query_str: str):
        try:
            qbase64 = base64.b64encode(query_str.encode()).decode()
            params = {
                'key': self.key, 
                'qbase64': qbase64,
                'fields': 'ip,port,body',
                'size': 100
            }
            if self.email:
                params['email'] = self.email

            res = requests.get(self.base_url, params=params, timeout=15)
            data = res.json()
            
            if data.get('error'):
                err_msg = data.get('errmsg', '')
                if '820001' in str(data.get('error')) or 'privilege' in err_msg.lower():
                    return "PERMISSION_ERROR"
                print(f"FOFA Error: {err_msg}")
                return []

            token_pattern = re.compile(r'\d{8,10}:[A-Za-z0-9_-]{35}')
            results = []
            for result in data.get('results', []):
                if len(result) < 3:
                    continue
                body = result[2]
                found = token_pattern.findall(body)
                for t in found:
                    if not _is_valid_token(t): continue
                    results.append({
                        "token": t,
                        "meta": {
                            "ip": result[0],
                            "port": result[1],
                            "source": "fofa"
                        }
                    })
            return results
        except Exception as e:
            print(f"FOFA Exception: {e}")
            return []

class UrlScanService:
    def __init__(self):
        self.api_key = settings.URLSCAN_KEY
        self.base_url = "https://urlscan.io/api/v1/search/"
        
    def search(self, query: str) -> List[Dict[str, Any]]:
        return []

class GithubService:
    def __init__(self):
        self.token = settings.GITHUB_TOKEN
        self.base_url = "https://api.github.com/search/code"
        
    def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.token:
            print("GitHub Token missing")
            return []
            
        try:
            headers = {
                'Authorization': f'token {self.token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            params = {'q': query, 'per_page': 30}
            
            res = requests.get(self.base_url, headers=headers, params=params, timeout=15)
            if res.status_code == 403 or res.status_code == 429:
                print("GitHub Rate Limit Exceeded")
                return []
            res.raise_for_status()
            
            data = res.json()
            items = data.get('items', [])
            
            results = []
            token_pattern = re.compile(r'\d{8,10}:[A-Za-z0-9_-]{35}')
            
            for item in items:
                raw_url = item.get('html_url', '').replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
                try:
                    raw_res = requests.get(raw_url, timeout=5)
                    content = raw_res.text
                    found = token_pattern.findall(content)
                    if not _is_valid_token(t): continue
                         results.append({
                            "token": t,
                            "meta": {
                                "source": "github",
                                "repo": item.get('repository', {}).get('full_name'),
                                "file_url": item.get('html_url')
                            }
                        })
                except Exception:
                    continue
            return results
        except Exception as e:
            print(f"GitHub Error: {e}")
            return []

class CensysService:
    def __init__(self):
        self.api_id = settings.CENSYS_ID
        self.api_secret = settings.CENSYS_SECRET
        self.base_url = "https://search.censys.io/api/v2/hosts/search"

    def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.api_id or not self.api_secret:
            return []

        try:
            auth = (self.api_id, self.api_secret)
            params = {'q': query, 'per_page': 50} 
            res = requests.get(self.base_url, auth=auth, params=params, timeout=15)
            res.raise_for_status()
            data = res.json()
            hits = data.get('result', {}).get('hits', [])
            # Strict Regex: \b (boundary) + digits + : + 35 chars + \b
            token_pattern = re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')
            results = []
            
            print(f"    [Censys] Processing {len(hits)} raw hits...")
            
            for hit in hits:
                ip = hit.get('ip')
                services = hit.get('services', [])
                
                # Active Verification: Try to connect to common web ports
                # Censys services often list ports like 80, 443, 8080. 
                # We can try to guess or use the `services` list if it contains port info (it is a list of dicts or objects).
                # Simplified: try 443 then 80.
                
                found_tokens = []
                
                # 1. Passive Checks (if we could parse services text, but structure varies)
                # ... skipping deep passive parsing to focus on active as requested ...

                # 2. Active Verification
                ports_to_try = [443, 80]
                # If we can parse real ports from 'services', add them:
                for svc in services:
                    if isinstance(svc, dict):
                        p = svc.get('port')
                        if p: ports_to_try.append(p)
                
                ports_to_try = list(set(ports_to_try))
                
                for port in ports_to_try:
                    try:
                        proto = "https" if port == 443 or port == 8443 else "http"
                        target_url = f"{proto}://{ip}:{port}"
                        
                        # Short timeout, verify=False for self-signed
                        tokens = _perform_active_deep_scan(target_url)
                        if tokens:
                             found_tokens.extend(tokens)
                             break # Found connection, stop trying ports for this IP
                    except Exception:
                        pass
                
                found_tokens = list(set(found_tokens))
                for t in found_tokens:
                    if not _is_valid_token(t): continue
                    results.append({
                        "token": t,
                        "meta": {
                            "source": "censys",
                            "ip": ip,
                            "censys_data": "verified_active"
                        }
                    })
            return results
        except Exception as e:
            print(f"Censys Error: {e}")
            return []

class HybridAnalysisService:
    def __init__(self):
        self.api_key = settings.HYBRID_ANALYSIS_KEY
        self.base_url = "https://www.hybrid-analysis.com/api/v2/search/terms"

    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Search Hybrid Analysis using 'api.telegram.org'.
        Endpoint: POST /api/v2/search/terms
        """
        if not self.api_key:
            return []

        # Ensure WWW is present to avoid redirection issues/404s
        url = "https://www.hybrid-analysis.com/api/v2/search/terms"
        
        try:
            # Headers required by v2
            headers = {
                'api-key': self.api_key,
                'User-Agent': 'Falcon Sandbox'
            }
            
            # Param 'term' is correct for this endpoint (form-data)
            data = {'term': query}
            
            print(f"    [HybridAnalysis] Querying: {url} with term='{query}'...")
            
            res = requests.post(url, headers=headers, data=data, timeout=30)
            
            if res.status_code == 404:
                # If it fails, we can't do much but log it.
                print(f"    ❌ [HybridAnalysis] Endpoint 404. Key permissions or API change?")
                return []
                
            res.raise_for_status()
            response_json = res.json()
            
            results = []
            
            # Response 'result' is list of matches
            # Each match has 'sha256', 'verdict', 'submit_name'
            hits = response_json.get('result', [])
            
            print(f"    [HybridAnalysis] Found {len(hits)} hits.")

            for item in hits:
                # We can't verify tokens without downloading the full sample (expensive/hard).
                # We flag for review.
                results.append({
                    "token": "MANUAL_REVIEW_REQUIRED",
                    "meta": {
                        "source": "hybrid_analysis",
                        "sha256": item.get('sha256'),
                        "verdict": item.get('verdict'),
                        "context": item.get('context'),
                        "report_url": f"https://www.hybrid-analysis.com/sample/{item.get('sha256')}"
                    }
                })
            return results

        except Exception as e:
            print(f"Hybrid Analysis Error: {e}")
            return []
