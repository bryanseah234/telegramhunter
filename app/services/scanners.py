from typing import List, Dict, Any
from app.core.config import settings
import requests
import base64
import re
import urllib3

# Suppress SSL warnings for active scanning of random IPs (self-signed certs etc)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)



# User-provided fingerprint for stealth settings
SPOOFED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",  # Derived from "lang": "en-GB"
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chrome";v="143", "Not=A?Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"'
}

def _is_valid_token(token_str: str) -> bool:
    """
    Strict validation to filter out Fernet strings, hashes, and junk.
    Valid Telegram token: 123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx (digits:35chars)
    """
    try:
        # Explicit Fernet rejection (starts with gAAAA)
        if token_str.startswith("gAAAA"):
            return False
        
        # Must contain exactly one colon
        if ":" not in token_str:
            return False
        if token_str.count(":") != 1:
            return False
            
        parts = token_str.split(":", 1)
        bot_id, secret = parts
        
        # Bot ID must be 8-10 digits, no leading zeros
        if not bot_id.isdigit():
            return False
        if len(bot_id) < 8 or len(bot_id) > 10:
            return False
        if len(bot_id) > 1 and bot_id.startswith("0"):
            return False
        
        # Secret must be exactly 35 characters
        if len(secret) != 35:
            return False
        
        # Secret must only contain allowed chars (base64-ish)
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
        if not all(c in allowed for c in secret):
            return False
        
        # Telegram secrets ALWAYS start with "AA"
        if not secret.startswith("AA"):
            return False
        
        # Suspicious: Pure hex (likely hash collision)
        is_pure_hex = all(c in "0123456789abcdefABCDEF" for c in secret)
        if is_pure_hex:
            # Real tokens have mixed case and special chars, pure hex is suspicious
            return False

        return True
    except Exception:
        return False

# Strict Regex: \b (boundary) + digits + : + 35 chars + \b
TOKEN_PATTERN = re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')

def _perform_active_deep_scan(target_url: str) -> List[str]:
    """
    Connects to a URL, scans HTML body, finds <script src="..."> tags, 
    fetches those JS files, and scans them too.
    Returns a list of unique valid tokens found.
    """
    found_tokens = []
    
    try:
        # 1. Fetch Main HTML
        print(f"      [DeepScan] Fetching: {target_url}")
        res = requests.get(target_url, headers=SPOOFED_HEADERS, timeout=10, verify=False)
        if res.status_code != 200: return []
        
        html_content = res.text
        found_tokens.extend(TOKEN_PATTERN.findall(html_content))
        
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
                from urllib.parse import urljoin
                js_url = urljoin(target_url, js_path)
            else:
                # Relative path
                from urllib.parse import urljoin
                js_url = urljoin(target_url, js_path)

            try:
                # Use same headers for JS fetching
                js_res = requests.get(js_url, headers=SPOOFED_HEADERS, timeout=5, verify=False)
                if js_res.status_code == 200:
                    found_tokens.extend(TOKEN_PATTERN.findall(js_res.text))
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
            results = []
            
            matches = data.get('matches', [])
            
            # Filter: past 2 hours OR 200 results (whichever is more)
            from datetime import datetime, timedelta
            two_hours_ago = datetime.utcnow() - timedelta(hours=2)
            
            # Sort by timestamp (most recent first)
            matches = sorted(matches, key=lambda x: x.get('timestamp', ''), reverse=True)
            
            # Filter to last 2 hours
            recent_matches = []
            for m in matches:
                try:
                    ts = m.get('timestamp', '')
                    if ts:
                        # Shodan format: "2024-12-31T21:00:00.000000"
                        match_time = datetime.fromisoformat(ts.replace('Z', '+00:00').split('+')[0])
                        if match_time >= two_hours_ago:
                            recent_matches.append(m)
                except:
                    pass
            
            # Take whichever is MORE: 2hr results or 200 cap
            if len(recent_matches) > len(matches[:200]):
                matches = recent_matches
            else:
                matches = matches[:200]
            
            print(f"    [Shodan] Processing {len(matches)} hits (2hr filter or max 200)...")

            for match in matches:
                ip = match.get('ip_str')
                port = match.get('port')
                banner = match.get('data', '')
                
                # 1. Passive Check (Banner)
                found = TOKEN_PATTERN.findall(banner)
                
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

            results = []
            for result in data.get('results', []):
                if len(result) < 3:
                    continue
                body = result[2]
                found = TOKEN_PATTERN.findall(body)
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
            params = {'q': query, 'per_page': 100, 'sort': 'indexed', 'order': 'desc'}  # Get more, filter later
            
            res = requests.get(self.base_url, headers=headers, params=params, timeout=15)
            if res.status_code == 403 or res.status_code == 429:
                print("GitHub Rate Limit Exceeded")
                return []
            res.raise_for_status()
            
            data = res.json()
            items = data.get('items', [])
            
            results = []
            for item in items:
                raw_url = item.get('html_url', '').replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
                try:
                    raw_res = requests.get(raw_url, timeout=5)
                    content = raw_res.text
                    found = TOKEN_PATTERN.findall(content)
                    for t in found:
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
        # Censys now uses a single API token (Personal Access Token)
        self.api_token = settings.CENSYS_ID  # Token stored in CENSYS_ID
        self.base_url = "https://search.censys.io/api/v2/hosts/search"

    def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.api_token:
            print("    [Censys] No API token found in CENSYS_ID")
            return []

        try:
            # Token-based auth via header (new Censys API)
            headers = {
                'Authorization': f'Bearer {self.api_token}',
                'Accept': 'application/json'
            }
            
            # Censys API requires field-specific queries
            # For finding "api.telegram.org" in HTTP responses/headers:
            api_query = (
                f'services.http.response.html_title: "{query}" OR '
                f'services.http.response.body: "{query}" OR '
                f'services.http.response.headers.value: "{query}" OR '
                f'web.endpoints.http.headers.value: "{query}"'
            )
            
            print(f"    [Censys] API Query: {api_query[:80]}...")
            
            params = {'q': api_query, 'per_page': 100} 
            res = requests.get(self.base_url, headers=headers, params=params, timeout=15)
            res.raise_for_status()
            data = res.json()
            hits = data.get('result', {}).get('hits', [])
            
            # Filter: past 2 hours OR 200 results (whichever is more)
            from datetime import datetime, timedelta
            two_hours_ago = datetime.utcnow() - timedelta(hours=2)
            
            # Sort by last_updated (most recent first)
            hits = sorted(hits, key=lambda x: x.get('last_updated_at', ''), reverse=True)
            
            # Filter to last 2 hours
            recent_hits = []
            for h in hits:
                try:
                    ts = h.get('last_updated_at', '')
                    if ts:
                        hit_time = datetime.fromisoformat(ts.replace('Z', '+00:00').split('+')[0])
                        if hit_time >= two_hours_ago:
                            recent_hits.append(h)
                except:
                    pass
            
            # Take whichever is MORE: 2hr results or 200 cap
            if len(recent_hits) > len(hits[:200]):
                hits = recent_hits
            else:
                hits = hits[:200]
            
            results = []
            
            print(f"    [Censys] Processing {len(hits)} hits (2hr filter or max 200)...")
            
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
            # Headers required by v2 - Content-Type is CRITICAL
            headers = {
                'api-key': self.api_key,
                'User-Agent': 'Falcon Sandbox',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            # Try multiple parameter formats (different key tiers have different access)
            search_params = [
                {'domain': query},
                {'url': f"https://{query}"},
                {'host': query}
            ]
            
            response_json = None
            for params in search_params:
                print(f"    [HybridAnalysis] POST to {url} with {list(params.keys())[0]}='{list(params.values())[0]}'...")
                
                res = requests.post(url, headers=headers, data=params, timeout=30)
                
                if res.status_code == 200:
                    response_json = res.json()
                    break
                elif res.status_code == 404:
                    print(f"    ⚠️ [HybridAnalysis] Param '{list(params.keys())[0]}' returned 404, trying next...")
                    continue
                else:
                    res.raise_for_status()
            
            if not response_json:
                print(f"    ❌ [HybridAnalysis] All parameters returned 404. Check API key permissions.")
                return []
            
            results = []
            
            # Response 'result' is list of matches
            hits = response_json.get('result', [])
            
            print(f"    [HybridAnalysis] Found {len(hits)} hits. Filtering executables...")

            # URL regex to extract potential targets from metadata
            url_pattern = re.compile(r'https?://[^\s"\'<>]+')
            
            # Executable file type keywords to filter out
            exe_keywords = ['exe', 'pe32', 'pe64', 'executable', 'dll', 'msi', 'bat', 'cmd', 'scr']
            
            filtered_count = 0
            for item in hits:
                # Skip executable files
                file_type = str(item.get('type', '')).lower()
                file_type_str = str(item.get('type_short', '')).lower()
                submit_name = str(item.get('submit_name', '')).lower()
                
                is_executable = any(kw in file_type or kw in file_type_str or submit_name.endswith(f'.{kw}') 
                                   for kw in exe_keywords)
                
                if is_executable:
                    filtered_count += 1
                    continue
                
                # Try to find URLs in context, submit_name, or other fields
                context = str(item.get('context', '')) + " " + str(item.get('submit_name', ''))
                found_urls = url_pattern.findall(context)
                
                # Also check for domain names that might be IPs
                hosts = item.get('hosts', []) or []
                domains = item.get('domains', []) or []
                
                # Build list of targets to deep scan
                targets = list(set(found_urls))
                for h in hosts:
                    if h: targets.append(f"http://{h}")
                for d in domains:
                    if d: targets.append(f"http://{d}")
                
                # Deep scan each target (limit to 3 per report)
                for target_url in targets[:3]:
                    try:
                        tokens = _perform_active_deep_scan(target_url)
                        for t in tokens:
                            if _is_valid_token(t):
                                results.append({
                                    "token": t,
                                    "meta": {
                                        "source": "hybrid_analysis",
                                        "sha256": item.get('sha256'),
                                        "verdict": item.get('verdict'),
                                        "scanned_url": target_url,
                                        "report_url": f"https://www.hybrid-analysis.com/sample/{item.get('sha256')}"
                                    }
                                })
                    except Exception:
                        pass
                
                # If no URLs found, still log the report for manual review (but don't save)
                if not targets:
                    print(f"      [HA] No scannable URLs in report {item.get('sha256', 'unknown')[:12]}...")

            print(f"    [HybridAnalysis] Filtered out {filtered_count} executables.")

            return results

        except Exception as e:
            print(f"Hybrid Analysis Error: {e}")
            return []
