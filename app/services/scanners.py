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
        # 0. Check URL string itself for tokens (e.g. leaking in GET params)
        found_tokens.extend(TOKEN_PATTERN.findall(target_url))

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
            
            # Filter: past 3 hours OR 300 results (whichever is more)
            from datetime import datetime, timedelta
            three_hours_ago = datetime.utcnow() - timedelta(hours=3)
            
            # Sort by timestamp (most recent first)
            matches = sorted(matches, key=lambda x: x.get('timestamp', ''), reverse=True)
            
            # Filter to last 3 hours
            recent_matches = []
            for m in matches:
                try:
                    ts = m.get('timestamp', '')
                    if ts:
                        # Shodan format: "2024-12-31T21:00:00.000000"
                        match_time = datetime.fromisoformat(ts.replace('Z', '+00:00').split('+')[0])
                        if match_time >= three_hours_ago:
                            recent_matches.append(m)
                except:
                    pass
            
            # Take whichever is MORE: 3hr results or 300 cap
            if len(recent_matches) > len(matches[:300]):
                matches = recent_matches
            else:
                matches = matches[:300]
            
            print(f"    [Shodan] Processing {len(matches)} hits (3hr filter or max 300)...")

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
            return []

# FofaService REMOVED - API access issues for free tier

class UrlScanService:
    """
    URLScan.io API - Search for pages containing api.telegram.org
    Free tier: 1000 searches/day, 100 results/search
    """
    def __init__(self):
        self.api_key = settings.URLSCAN_KEY
        self.search_url = "https://urlscan.io/api/v1/search/"
        
    def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.api_key:
            print("    [URLScan] No API key found")
            return []
            
        try:
            headers = {
                'API-Key': self.api_key,
                'Content-Type': 'application/json'
            }
            
            # URLScan query format: search in page content
            # Query format: page.domain:X OR page.url:*X* OR filename:X
            api_query = f'page.body:"{query}" OR page.url:*{query}*'
            
            params = {
                'q': api_query,
                'size': 500
            }
            
            print(f"    [URLScan] Searching: {api_query[:50]}...")
            
            res = requests.get(self.search_url, headers=headers, params=params, timeout=15)
            
            if res.status_code == 401:
                print(f"    ❌ [URLScan] 401 Unauthorized - Check API key")
                return []
            elif res.status_code == 429:
                print(f"    ❌ [URLScan] Rate limit exceeded")
                return []
            
            res.raise_for_status()
            data = res.json()
            
            results_list = data.get('results', [])
            print(f"    [URLScan] Found {len(results_list)} hits. Deep scanning...")
            
            # Sort by scan time (most recent first) and limit
            from datetime import datetime, timedelta
            three_hours_ago = datetime.utcnow() - timedelta(hours=3)
            
            results_list = sorted(results_list, key=lambda x: x.get('task', {}).get('time', ''), reverse=True)
            
            # Filter to recent or max 300
            recent_results = []
            for r in results_list:
                try:
                    ts = r.get('task', {}).get('time', '')
                    if ts:
                        scan_time = datetime.fromisoformat(ts.replace('Z', '+00:00').split('+')[0])
                        if scan_time >= three_hours_ago:
                            recent_results.append(r)
                except:
                    pass
            
            if len(recent_results) > len(results_list[:300]):
                results_list = recent_results
            else:
                results_list = results_list[:300]
            
            results = []
            
            for item in results_list:
                page_url = item.get('page', {}).get('url', '')
                if not page_url:
                    continue
                
                # Deep scan the URL for tokens
                try:
                    tokens = _perform_active_deep_scan(page_url)
                    for t in tokens:
                        if _is_valid_token(t):
                            results.append({
                                "token": t,
                                "meta": {
                                    "source": "urlscan",
                                    "url": page_url,
                                    "domain": item.get('page', {}).get('domain'),
                                    "scan_id": item.get('_id')
                                }
                            })
                except Exception:
                    continue
            
            print(f"    [URLScan] Deep scan complete. {len(results)} valid tokens found.")
            return results
            
        except Exception as e:
            print(f"URLScan Error: {e}")
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

# CensysService REMOVED - Free tier doesn't have search API access

# HybridAnalysisService REMOVED - API key permission issues with /search/terms endpoint
