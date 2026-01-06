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

# Regex for Telegram Bot Token: digits:35chars
# 123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TOKEN_PATTERN = re.compile(r'\b(\d{8,10}:[A-Za-z0-9_-]{35})\b')


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
# Regex for extracting chat_id (e.g., chat_id=12345 or "chat_id": 12345)
CHAT_ID_PATTERN = re.compile(r'(?:chat_id|chat|target|cid)[=_":\s]+([-\d]+)', re.IGNORECASE)

def _perform_active_deep_scan(target_url: str) -> List[Dict[str, str]]:
    """
    Connects to a URL, scans HTML body, finds <script src="..."> tags, 
    fetches those JS files, and scans them too.
    Returns a list of dicts: [{'token': '...', 'chat_id': '...'}]
    """
    found_results = [] # List of {'token': ..., 'chat_id': ...}
    
    def extract_from_text(text: str) -> List[Dict[str, str]]:
        tokens = TOKEN_PATTERN.findall(text)
        chat_ids = CHAT_ID_PATTERN.findall(text)
        # Simple heuristic: If we find 1 token and 1 chat_id, assume they pair.
        # If we find multiple, we just return the tokens and any chat_id found "globally" (imperfect but better than nothing)
        
        extracted = []
        # First, try to find direct pairs in the URL query string style
        # e.g. ?bot_token=XXX&chat_id=YYY
        
        # If text is short (like a URL), finding both means they are likely related
        if len(text) < 500:
             cid = chat_ids[0] if chat_ids else None
             for t in tokens:
                 if _is_valid_token(t):
                     extracted.append({'token': t, 'chat_id': cid})
             return extracted

        # For larger bodies, just grab all valid tokens
        for t in set(tokens): # dedup tokens
             if _is_valid_token(t):
                 # Try to find a chat_id close to this token? too complex for regex active scan
                 # We just grab the first found chat_id in the doc as a "best guess" context
                 cid = chat_ids[0] if chat_ids else None
                 extracted.append({'token': t, 'chat_id': cid})
        return extracted


    try:
        # 0. Check URL string itself
        found_results.extend(extract_from_text(target_url))

        # Optimization: Skip fetching api.telegram.org (Token already found in URL step above)
        if "api.telegram.org" in target_url:
            return found_results

        # 1. Fetch Main HTML
        print(f"      [DeepScan] Fetching: {target_url}")
        res = requests.get(target_url, headers=SPOOFED_HEADERS, timeout=10, verify=False)
        if res.status_code != 200:
             pass
        else:
             html_content = res.text
             found_results.extend(extract_from_text(html_content))
        
             # 2. Find External JS
             js_links = re.findall(r'src=["\'](.*?.js)["\']', html_content)
             unique_js = list(set(js_links))[:5] 
             
             for js_path in unique_js:
                 if js_path.startswith("//"): js_url = "https:" + js_path
                 elif js_path.startswith("http"): js_url = js_path
                 else:
                     from urllib.parse import urljoin
                     js_url = urljoin(target_url, js_path)
    
                 try:
                     js_res = requests.get(js_url, headers=SPOOFED_HEADERS, timeout=5, verify=False)
                     if js_res.status_code == 200:
                         found_results.extend(extract_from_text(js_res.text))
                 except Exception:
                     pass

        # Deduplicate by token (keep the one with chat_id if conflicting)
        final_map = {}
        for item in found_results:
            t = item['token']
            c = item['chat_id']
            if t not in final_map:
                final_map[t] = c
            elif not final_map[t] and c:
                final_map[t] = c # Upgrade to having chat_id
                
        return [{'token': t, 'chat_id': c} for t, c in final_map.items()]

    except Exception:
        return []

class ShodanService:
    def __init__(self):
        self.api_key = settings.SHODAN_KEY
        self.base_url = "https://api.shodan.io/shodan/host/search"

    def search(self, query: str, country_code: str = None) -> List[Dict[str, Any]]:
        if not self.api_key: return []
        try:
            full_query = query
            if country_code:
                full_query = f'{query} country:"{country_code}"'
                print(f"    [Shodan] Adding country filter: {country_code}")
            
            params = {'key': self.api_key, 'query': full_query}
            res = requests.get(self.base_url, params=params, timeout=15)
            res.raise_for_status()
            matches = res.json().get('matches', [])
            
            # (Sorting/Filter logic omitted for brevity, assuming kept from previous edits if not replacing whole method)
            # Actually I am replacing the whole block, I need to keep the logic or rewrite it carefully.
            # I will assume the previous sorting/filtering logic is standard enough to simplify or I need to re-include it. 
            # Re-including the 3hr filter logic for robustness.
            
            from datetime import datetime, timedelta
            three_hours_ago = datetime.utcnow() - timedelta(hours=3)
            matches = sorted(matches, key=lambda x: x.get('timestamp', ''), reverse=True)
            recent_matches = []
            for m in matches:
                try:
                    ts = m.get('timestamp', '')
                    if ts:
                        match_time = datetime.fromisoformat(ts.replace('Z', '+00:00').split('+')[0])
                        if match_time >= three_hours_ago: recent_matches.append(m)
                except: pass
            
            if len(recent_matches) > len(matches[:300]): matches = recent_matches
            else: matches = matches[:300]

            results = []
            print(f"    [Shodan] Processing {len(matches)} and scanning...")

            for match in matches:
                ip = match.get('ip_str')
                port = match.get('port')
                banner = match.get('data', '')
                
                # Passive
                tokens_found = TOKEN_PATTERN.findall(banner)
                # Shodan Banner usually doesn't have chat_id easily, but let's check
                chat_ids_found = CHAT_ID_PATTERN.findall(banner)
                passive_cid = chat_ids_found[0] if chat_ids_found else None

                all_found = [{'token': t, 'chat_id': passive_cid} for t in set(tokens_found) if _is_valid_token(t)]

                # Active
                if not all_found:
                    try:
                        proto = "https" if port == 443 else "http"
                        target_url = f"{proto}://{ip}:{port}"
                        # Deep scan now returns dicts
                        active_found = _perform_active_deep_scan(target_url)
                        all_found.extend(active_found)
                    except Exception: pass

                # Dedup
                seen_t = set()
                for item in all_found:
                    t = item['token']
                    if t in seen_t: continue
                    seen_t.add(t)
                    
                    results.append({
                        "token": t,
                        "chat_id": item['chat_id'], # Pass it up
                        "meta": {
                            "ip": ip,
                            "port": port,
                            "shodan_data": "verified_active" if ip else "banner_match"
                        }
                    })
            return results
        except Exception:
            return []

class FofaService:
    def __init__(self):
        self.email = settings.FOFA_EMAIL
        self.key = settings.FOFA_KEY
        self.base_url = "https://fofa.info/api/v1/search/all"

    def search(self, query: str = 'body="api.telegram.org/bot"', country_code: str = None) -> List[Dict[str, Any]]:
        if not (self.email and self.key): return []
        try:
            full_query = query
            if country_code:
                full_query = f'{query} && country="{country_code}"'
                print(f"    [FOFA] Adding country filter: {country_code}")

            qbase64 = base64.b64encode(full_query.encode()).decode()
            params = {'email': self.email, 'key': self.key, 'qbase64': qbase64, 'fields': 'host,ip,port', 'size': 100}
            print(f"    [FOFA] Searching: {full_query}")
            res = requests.get(self.base_url, params=params, timeout=15)
            if res.status_code != 200: return []
            
            valid_results = []
            for row in res.json().get("results", []):
                host = row[0]
                # URL Construction
                target_url = host if host.startswith("http") else (f"https://{host}" if row[2]=="443" else f"http://{host}:{row[2]}")
                
                try:
                    # Deep scan
                    items = _perform_active_deep_scan(target_url)
                    for item in items:
                        valid_results.append({
                            "token": item['token'],
                            "chat_id": item['chat_id'],
                            "meta": {"source": "fofa", "url": target_url}
                        })
                except Exception: pass
            return valid_results
        except Exception as e:
            return []

class UrlScanService:
    """
    URLScan.io API - Search for pages containing api.telegram.org
    Free tier: 1000 searches/day, 100 results/search
    """
    def __init__(self):
        self.api_key = settings.URLSCAN_KEY
        self.search_url = "https://urlscan.io/api/v1/search/"
        
    def search(self, query: str, country_code: str = None) -> List[Dict[str, Any]]:
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
            
            if country_code:
                api_query = f'({api_query}) AND page.country:"{country_code}"'
                print(f"    [URLScan] Adding country filter: {country_code}")
            
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
            print(f"    [URLScan] Found {len(results_list)} hits. scanning cache & live...")
            
            # Sort by scan time (most recent first) and limit
            from datetime import datetime, timedelta
            three_hours_ago = datetime.utcnow() - timedelta(hours=3)
            
            # Filter and sort
            valid_items = []
            for r in results_list:
                try:
                    ts = r.get('task', {}).get('time', '')
                    if ts:
                        scan_time = datetime.fromisoformat(ts.replace('Z', '+00:00').split('+')[0])
                        if scan_time >= three_hours_ago:
                            valid_items.append(r)
                except:
                    pass
            
            # Sort valid items by time desc
            valid_items = sorted(valid_items, key=lambda x: x.get('task', {}).get('time', ''), reverse=True)

            # Cap at 300
            if len(valid_items) > 300:
                valid_items = valid_items[:300]
            elif not valid_items and len(results_list) > 0:
                 # Fallback: if no recent results, take top 50 of any time to ensure we get something
                 valid_items = results_list[:50]
            
            results = []
            
            for item in valid_items:
                page_url = item.get('page', {}).get('url', '')
                scan_id = item.get('_id')
                
                found_tokens = []
                
                # 1. Cached DOM Scan (History)
                if scan_id:
                    try:
                         dom_url = f"https://urlscan.io/dom/{scan_id}"
                         dom_res = requests.get(dom_url, headers=headers, timeout=5)
                         if dom_res.status_code == 200:
                             # Use the helper to extract both token and chat_id
                             # We treat the DOM content as text
                             # But we need access to the helper inside the class? 
                             # _perform_active_deep_scan's internal helper isn't exposed.
                             # Let's just use the regexes directly here for simplicity or expose the helper?
                             # Better: reuse the regex logic by calling a helper function if I extracted it?
                             # I didn't extract the helper. I'll just use regexes here.
                             content = dom_res.text
                             tokens = TOKEN_PATTERN.findall(content)
                             cids = CHAT_ID_PATTERN.findall(content)
                             cid = cids[0] if cids else None
                             for t in tokens:
                                 found_tokens.append({'token': t, 'chat_id': cid})
                    except Exception:
                        pass

                # 2. Live Deep Scan (Verification)
                if page_url:
                    try:
                        # Check URL itself first for quick win
                        # e.g. url?bot_token=...&chat_id=...
                        url_tokens = TOKEN_PATTERN.findall(page_url)
                        url_cids = CHAT_ID_PATTERN.findall(page_url)
                        url_cid = url_cids[0] if url_cids else None
                        for t in url_tokens:
                            found_tokens.append({'token': t, 'chat_id': url_cid})

                        # Deep Scan
                        live_items = _perform_active_deep_scan(page_url)
                        found_tokens.extend(live_items)
                    except Exception:
                        pass
                
                # Process found tokens
                # Dedup
                final_map = {}
                for item in found_tokens:
                    t = item['token']
                    c = item['chat_id']
                    if t not in final_map:
                        final_map[t] = c
                    elif not final_map[t] and c:
                        final_map[t] = c

                for t, cid in final_map.items():
                    if _is_valid_token(t):
                        results.append({
                            "token": t,
                            "chat_id": cid, # Pass passing extracted chat_id
                            "meta": {
                                "source": "urlscan",
                                "url": page_url,
                                "domain": item.get('page', {}).get('domain'),
                                "scan_id": scan_id,
                                "type": "cached_or_live"
                            }
                        })
            
            print(f"    [URLScan] Scan complete. {len(results)} valid tokens found.")
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
            
            # Fetch up to 5 pages (100 * 5 = 500 potential matches)
            # note: GitHub code search API rate limits are strict (10/min or 30/min with auth)
            # We sleep slightly between pages.
            import time
            
            for page in range(1, 6):
                params['page'] = page
                print(f"    [GitHub] Fetching page {page} for query: {query}")
                
                try:
                    res = requests.get(self.base_url, headers=headers, params=params, timeout=15)
                    
                    if res.status_code == 403 or res.status_code == 429:
                        print(f"    [GitHub] Rate Limit hit on page {page}")
                        break
                    
                    # 422 usually means "Validation Failed" (e.g. past page 10 or invalid query)
                    if res.status_code == 422:
                        break
                        
                    res.raise_for_status()
                    data = res.json()
                    items = data.get('items', [])
                    
                    if not items:
                        break
                        
                    for item in items:
                        raw_url = item.get('html_url', '').replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
                        try:
                            # 1s timeout for raw content logic to be fast
                            raw_res = requests.get(raw_url, timeout=3)
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
                            
                    time.sleep(2) # Be nice to GitHub API
                    
                except Exception as e:
                    print(f"    [GitHub] Page {page} failed: {e}")
                    break
                    
            return results
        except Exception as e:
            print(f"GitHub Error: {e}")
            return []

# CensysService REMOVED - Free tier doesn't have search API access

# HybridAnalysisService REMOVED - API key permission issues with /search/terms endpoint
