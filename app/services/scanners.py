import httpx
import asyncio
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

async def _perform_active_deep_scan(target_url: str, client: httpx.AsyncClient = None) -> List[Dict[str, str]]:
    """
    Connects to a URL, scans HTML body, finds <script src="..."> tags, 
    fetches those JS files, and scans them too.
    Returns a list of dicts: [{'token': '...', 'chat_id': '...'}]
    """
    found_results = [] # List of {'token': ..., 'chat_id': ...}
    
    def extract_from_text(text: str) -> List[Dict[str, str]]:
        tokens = TOKEN_PATTERN.findall(text)
        chat_ids = CHAT_ID_PATTERN.findall(text)
        
        extracted = []
        if len(text) < 500:
             cid = chat_ids[0] if chat_ids else None
             for t in tokens:
                 if _is_valid_token(t):
                     extracted.append({'token': t, 'chat_id': cid})
             return extracted

        for t in set(tokens): # dedup tokens
             if _is_valid_token(t):
                 cid = chat_ids[0] if chat_ids else None
                 extracted.append({'token': t, 'chat_id': cid})
        return extracted

    # Use provided client or create a temporary one
    should_close = False
    if client is None:
        client = httpx.AsyncClient(verify=False, timeout=10.0)
        should_close = True

    try:
        # 0. Check URL string itself
        found_results.extend(extract_from_text(target_url))

        if "api.telegram.org" in target_url:
            if should_close: await client.aclose()
            return found_results

        # 1. Fetch Main HTML
        # print(f"      [DeepScan] Fetching: {target_url}")
        try:
            res = await client.get(target_url, headers=SPOOFED_HEADERS, follow_redirects=True)
            if res.status_code == 200:
                html_content = res.text
                found_results.extend(extract_from_text(html_content))
            
                # 2. Find External JS
                js_links = re.findall(r'src=["\'](.*?.js)["\']', html_content)
                unique_js = list(set(js_links))[:5] 
                
                # Create async tasks for JS fetching
                js_tasks = []
                for js_path in unique_js:
                    if js_path.startswith("//"): js_url = "https:" + js_path
                    elif js_path.startswith("http"): js_url = js_path
                    else:
                        from urllib.parse import urljoin
                        js_url = urljoin(target_url, js_path)
                    
                    js_tasks.append(client.get(js_url, headers=SPOOFED_HEADERS, follow_redirects=True))
                
                if js_tasks:
                    js_responses = await asyncio.gather(*js_tasks, return_exceptions=True)
                    for js_res in js_responses:
                        if isinstance(js_res, httpx.Response) and js_res.status_code == 200:
                            found_results.extend(extract_from_text(js_res.text))

        except Exception:
            pass

        # Deduplicate
        final_map = {}
        for item in found_results:
            t = item['token']
            c = item['chat_id']
            if t not in final_map:
                final_map[t] = c
            elif not final_map[t] and c:
                final_map[t] = c 
                
        return [{'token': t, 'chat_id': c} for t, c in final_map.items()]

    except Exception:
        return []
    finally:
        if should_close:
            await client.aclose()


class ShodanService:
    def __init__(self):
        self.api_key = settings.SHODAN_KEY
        self.base_url = "https://api.shodan.io/shodan/host/search"

    async def search(self, query: str, country_code: str = None) -> List[Dict[str, Any]]:
        if not self.api_key: return []
        try:
            full_query = query
            if country_code:
                full_query = f'{query} country:"{country_code}"'
                print(f"    [Shodan] Adding country filter: {country_code}")
            
            # Shodan API call must be async or threaded. Since it's one call, requests is fine IF wrapped,
            # but ideally use httpx.
            params = {'key': self.api_key, 'query': full_query}
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.base_url, params=params)
                res.raise_for_status()
                matches = res.json().get('matches', [])
            
            # Sort/Filter
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
            print(f"    [Shodan] Processing {len(matches)} matches...")

            # CONCURRENT PROCESSING
            async with httpx.AsyncClient(verify=False, timeout=10.0) as scan_client:
                tasks = []
                sem = asyncio.Semaphore(20) # Limit concurrency

                async def process_match(match):
                    async with sem:
                        ip = match.get('ip_str')
                        port = match.get('port')
                        banner = match.get('data', '')
                        
                        # Passive
                        tokens_found = TOKEN_PATTERN.findall(banner)
                        chat_ids_found = CHAT_ID_PATTERN.findall(banner)
                        passive_cid = chat_ids_found[0] if chat_ids_found else None

                        local_found = [{'token': t, 'chat_id': passive_cid} for t in set(tokens_found) if _is_valid_token(t)]

                        # Active Deep Scan
                        if not local_found:
                            try:
                                proto = "https" if port == 443 else "http"
                                target_url = f"{proto}://{ip}:{port}"
                                active_found = await _perform_active_deep_scan(target_url, client=scan_client)
                                local_found.extend(active_found)
                            except Exception: pass
                        
                        return (ip, port, local_found)

                for m in matches:
                    tasks.append(process_match(m))
                
                if tasks:
                    batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    batch_results = []
                
            # Aggregate Results
            for res_item in batch_results:
                if isinstance(res_item, Exception): continue
                if not isinstance(res_item, tuple): continue # Should be tuple
                
                ip, port, found_items = res_item
                
                # Dedup within this match
                seen_t = set()
                for item in found_items:
                    t = item['token']
                    if t in seen_t: continue
                    seen_t.add(t)
                    
                    results.append({
                        "token": t,
                        "chat_id": item['chat_id'],
                        "meta": {
                            "ip": ip,
                            "port": port,
                            "shodan_data": "verified_active" if ip else "banner_match"
                        }
                    })
            return results
        except Exception as e:
            print(f"    [Shodan] Error: {e}")
            return []

class FofaService:
    def __init__(self):
        self.email = settings.FOFA_EMAIL
        self.key = settings.FOFA_KEY
        self.base_url = "https://fofa.info/api/v1/search/all"

    async def search(self, query: str = 'body="api.telegram.org/bot"', country_code: str = None) -> List[Dict[str, Any]]:
        if not (self.email and self.key): return []
        try:
            full_query = query
            if country_code:
                full_query = f'{query} && country="{country_code}"'
                print(f"    [FOFA] Adding country filter: {country_code}")

            qbase64 = base64.b64encode(full_query.encode()).decode()
            params = {'email': self.email, 'key': self.key, 'qbase64': qbase64, 'fields': 'host,ip,port', 'size': 100}
            print(f"    [FOFA] Searching: {full_query}")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.base_url, params=params)
                if res.status_code != 200: return []
                results_data = res.json().get("results", [])
            
            valid_results = []
            
            # Parallel Active Scan
            async with httpx.AsyncClient(verify=False, timeout=10.0) as scan_client:
                tasks = []
                sem = asyncio.Semaphore(20)

                async def process_row(row):
                    async with sem:
                        host = row[0]
                        # URL Construction
                        target_url = host if host.startswith("http") else (f"https://{host}" if row[2]=="443" else f"http://{host}:{row[2]}")
                        
                        try:
                            # Deep scan
                            items = await _perform_active_deep_scan(target_url, client=scan_client)
                            return (target_url, items)
                        except Exception: 
                            return None

                for row in results_data:
                    tasks.append(process_row(row))
                
                scan_results = await asyncio.gather(*tasks, return_exceptions=True)

            for item in scan_results:
                if not item or isinstance(item, Exception): continue
                target_url, items = item
                for t_item in items:
                    valid_results.append({
                        "token": t_item['token'],
                        "chat_id": t_item['chat_id'],
                        "meta": {"source": "fofa", "url": target_url}
                    })

            return valid_results
        except Exception as e:
            print(f"    [FOFA] Error: {e}")
            return []

class UrlScanService:
    """
    URLScan.io API - Search for pages containing api.telegram.org
    Free tier: 1000 searches/day, 100 results/search
    """
    def __init__(self):
        self.api_key = settings.URLSCAN_KEY
        self.search_url = "https://urlscan.io/api/v1/search/"
        
    async def search(self, query: str, country_code: str = None) -> List[Dict[str, Any]]:
        if not self.api_key:
            print("    [URLScan] No API key found")
            return []
            
        try:
            headers = {
                'API-Key': self.api_key,
                'Content-Type': 'application/json'
            }
            
            # URLScan query format: search in page content
            api_query = f'page.body:"{query}" OR page.url:*{query}*'
            if country_code:
                api_query = f'({api_query}) AND page.country:"{country_code}"'
                print(f"    [URLScan] Adding country filter: {country_code}")
            
            params = {'q': api_query, 'size': 500}
            print(f"    [URLScan] Searching: {api_query[:50]}...")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.search_url, headers=headers, params=params)
                
                if res.status_code == 401: return []
                if res.status_code == 429: return []
                res.raise_for_status()
                data = res.json()
            
            results_list = data.get('results', [])
            print(f"    [URLScan] Found {len(results_list)} hits. scanning cache & live...")
            
            # Filter Logic (Date etc)
            from datetime import datetime, timedelta
            three_hours_ago = datetime.utcnow() - timedelta(hours=3)
            
            valid_items = []
            for r in results_list:
                try:
                    ts = r.get('task', {}).get('time', '')
                    if ts:
                        scan_time = datetime.fromisoformat(ts.replace('Z', '+00:00').split('+')[0])
                        if scan_time >= three_hours_ago:
                            valid_items.append(r)
                except: pass
            
            # Sort and Cap
            valid_items = sorted(valid_items, key=lambda x: x.get('task', {}).get('time', ''), reverse=True)
            if len(valid_items) > 300: valid_items = valid_items[:300]
            elif not valid_items and len(results_list) > 0: valid_items = results_list[:50]
            
            final_results = []
            
            # Parallel processing of DOM cache and Live Scan
            async with httpx.AsyncClient(verify=False, timeout=10.0) as scan_client:
                tasks = []
                sem = asyncio.Semaphore(20)

                async def process_urlscan_item(item):
                    async with sem:
                        page_url = item.get('page', {}).get('url', '')
                        scan_id = item.get('_id')
                        item_found_tokens = []
                        
                        # 1. Cached DOM Scan
                        if scan_id:
                            try:
                                dom_url = f"https://urlscan.io/dom/{scan_id}"
                                dom_res = await scan_client.get(dom_url, headers=headers)
                                if dom_res.status_code == 200:
                                     content = dom_res.text
                                     tokens = TOKEN_PATTERN.findall(content)
                                     cids = CHAT_ID_PATTERN.findall(content)
                                     cid = cids[0] if cids else None
                                     for t in tokens:
                                         item_found_tokens.append({'token': t, 'chat_id': cid})
                            except Exception: pass
                        
                        # 2. Live Deep Scan
                        if page_url:
                            try:
                                # URL Regex
                                url_tokens = TOKEN_PATTERN.findall(page_url)
                                url_cids = CHAT_ID_PATTERN.findall(page_url)
                                url_cid = url_cids[0] if url_cids else None
                                for t in url_tokens:
                                    item_found_tokens.append({'token': t, 'chat_id': url_cid})
                                
                                # Deep Scan
                                live_items = await _perform_active_deep_scan(page_url, client=scan_client)
                                item_found_tokens.extend(live_items)
                            except Exception: pass
                        
                        return (item, item_found_tokens)

                for item in valid_items:
                    tasks.append(process_urlscan_item(item))
                
                # Execute
                task_results = await asyncio.gather(*tasks, return_exceptions=True)

            for res_item in task_results:
                if not res_item or isinstance(res_item, Exception): continue
                
                item, found = res_item
                # Dedup
                final_map = {}
                for f_item in found:
                    t = f_item['token']
                    c = f_item['chat_id']
                    if t not in final_map:
                        final_map[t] = c
                    elif not final_map[t] and c:
                        final_map[t] = c

                for t, cid in final_map.items():
                    if _is_valid_token(t):
                         final_results.append({
                            "token": t,
                            "chat_id": cid,
                            "meta": {
                                "source": "urlscan",
                                "url": item.get('page', {}).get('url', ''),
                                "domain": item.get('page', {}).get('domain'),
                                "scan_id": item.get('_id'),
                                "type": "cached_or_live"
                            }
                        })
            
            print(f"    [URLScan] Scan complete. {len(final_results)} valid tokens found.")
            return final_results
            
        except Exception as e:
            print(f"URLScan Error: {e}")
            return []

class GithubService:
    def __init__(self):
        self.token = settings.GITHUB_TOKEN
        self.base_url = "https://api.github.com/search/code"
        
    async def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.token:
            print("GitHub Token missing")
            return []
            
        try:
            headers = {
                'Authorization': f'token {self.token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            params = {'q': query, 'per_page': 100, 'sort': 'indexed', 'order': 'desc'} 
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.base_url, headers=headers, params=params)
                if res.status_code in [403, 429]:
                    print("GitHub Rate Limit Exceeded")
                    return []
                res.raise_for_status()
                
                # We only fetch page 1 in async version for speed/safety, 
                # OR we implement pagination carefully. 
                # Let's fetch up to 3 pages to be safe but efficient.
                
                items = res.json().get('items', [])
                
                # Parallel Raw Fetching
                results = []
                async with httpx.AsyncClient(verify=False, timeout=10.0) as raw_client:
                    tasks = []
                    sem = asyncio.Semaphore(10) # GitHub raw is sensitive to speed?

                    async def fetch_raw(item):
                        async with sem:
                            raw_url = item.get('html_url', '').replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
                            try:
                                raw_res = await raw_client.get(raw_url)
                                content = raw_res.text
                                found = TOKEN_PATTERN.findall(content)
                                local_res = []
                                for t in found:
                                    if not _is_valid_token(t): continue
                                    local_res.append({
                                        "token": t,
                                        "meta": {
                                            "source": "github",
                                            "repo": item.get('repository', {}).get('full_name'),
                                            "file_url": item.get('html_url')
                                        }
                                    })
                                return local_res
                            except Exception:
                                return []
                    
                    for item in items:
                        tasks.append(fetch_raw(item))
                        
                    scan_results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    for batch in scan_results:
                        if batch and isinstance(batch, list):
                            results.extend(batch)
                            
            return results
        except Exception as e:
            print(f"GitHub Error: {e}")
            return []

# CensysService REMOVED - Free tier doesn't have search API access

# HybridAnalysisService REMOVED - API key permission issues with /search/terms endpoint
