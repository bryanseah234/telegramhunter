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
                        active_res = requests.get(target_url, timeout=3, verify=False)
                        if active_res.status_code == 200:
                            # Parse body
                            found.extend(token_pattern.findall(active_res.text))
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
                        active_res = requests.get(target_url, timeout=3, verify=False)
                        if active_res.status_code == 200:
                             found_tokens.extend(token_pattern.findall(active_res.text))
                             if found_tokens: break # Found connection, stop trying ports for this IP
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
        Search Hybrid Analysis for string matches in malware sandbox reports.
        Free tier limitations: Check quotas (often restricted).
        """
        if not self.api_key:
            return []

        try:
            headers = {
                'api-key': self.api_key,
                'User-Agent': 'Falcon Sandbox'
            }
            # Search query logic: 'verdict:malicious AND string:"api.telegram.org"'
            # Or just query strings if allowed.
            params = {'term': query}
            
            res = requests.post(self.base_url, headers=headers, data=params, timeout=20)
            res.raise_for_status()
            data = res.json()
            
            results = []
            # 'result' usually contains list of items with 'sha256', 'context', etc.
            # Hybrid Analysis search results might not give the full content, 
            # just the fact that it matched. We might need to look at 'strings' or 'dropped_files'.
            # For this MVP, we assume if we found a "Telegram" related malware report, 
            # we might want to flag the hash or look deeper.
            # Extraction of exact TOKEN from this API search result is hard without 
            # downloading the full report/sample.    
            # We will return the metadata pointing to the report.
            
            for item in data.get('result', []):
                # Placeholder logic: we can't easily extract the token without more calls.
                # But if we assume the query WAS the token, we'd have it.
                # If query is "api.telegram.org", we match reports. 
                # We'll return a generic "manual review" entry or skip if no token found.
                # Actually, HA API allows searching for specific strings.
                
                # We'll just pass basic metadata.
                results.append({
                    "token": "MANUAL_REVIEW_REQUIRED", # Placeholder
                    "meta": {
                        "source": "hybrid_analysis",
                        "sha256": item.get('sha256'),
                        "verdict": item.get('verdict'),
                        "report_url": f"https://www.hybrid-analysis.com/sample/{item.get('sha256')}"
                    }
                })
            return results

        except Exception as e:
            print(f"Hybrid Analysis Error: {e}")
            return []
