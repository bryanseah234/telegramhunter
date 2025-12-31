from typing import List, Dict, Any
from app.core.config import settings
import requests
import base64

class ShodanService:
    def __init__(self):
        self.api_key = settings.SHODAN_KEY
        self.base_url = "https://api.shodan.io/shodan/host/search"

    def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []

        try:
            params = {'key': self.api_key, 'query': query}
            # Timeout to prevent hanging
            res = requests.get(self.base_url, params=params, timeout=15)
            res.raise_for_status()
            data = res.json()
            
            # Simple extraction strategy: assume 'matches' has raw data.
            # In reality, finding tokens in Shodan requires regex on 'data' or 'banner'
            # Here, we assume the query targets something specific and we pass back metadata.
            # The Worker Task handles exact token extraction if it's not pre-parsed.
            # But the contract says we return a list of items with potential tokens.
            # If the user is just searching for "product:Telegram", we get IPs.
            # We'll just return matches for the worker to inspect/logging.
            # BUT: The worker expects a 'token'.
            # If we don't extract it here, the worker loop skips it.
            # Let's try a regex for bot tokens in the 'data' field.
            import re
            token_pattern = re.compile(r'\d{8,10}:[A-Za-z0-9_-]{35}')
            
            results = []
            for match in data.get('matches', []):
                banner = match.get('data', '')
                found = token_pattern.findall(banner)
                for t in found:
                    results.append({
                        "token": t, 
                        "meta": {
                            "ip": match.get('ip_str'),
                            "port": match.get('port'),
                            "shodan_data": "banner_match"
                        }
                    })
            return results

        except Exception as e:
            print(f"Shodan Error: {e}")
            return []

class FofaService:
    def __init__(self):
        self.email = settings.FOFA_EMAIL # Use email if needed, or just key
        self.key = settings.FOFA_KEY
        self.base_url = "https://fofa.info/api/v1/search/all"

    def search(self, query_str: str) -> List[Dict[str, Any]]:
        """
        FOFA requires base64 encoded query.
        """
        if not self.key:
            return []
        
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
            res.raise_for_status()
            data = res.json()
            
            if data.get('error'):
                print(f"FOFA Error: {data.get('errmsg')}")
                return []

            import re
            token_pattern = re.compile(r'\d{8,10}:[A-Za-z0-9_-]{35}')
            
            results = []
            for result in data.get('results', []):
                # result is [ip, port, body] based on 'fields'
                if len(result) < 3:
                    continue
                body = result[2]
                found = token_pattern.findall(body)
                for t in found:
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
        if not self.api_key:
            return []
            
        try:
            headers = {'API-Key': self.api_key}
            params = {'q': query, 'size': 100}
            res = requests.get(self.base_url, headers=headers, params=params, timeout=15)
            res.raise_for_status()
            data = res.json()
            
            # URLScan doesn't give 'body' easily in search, usually links to result.
            # This is complex. We might just search for 'task' results.
            # Placeholder for now as it requires secondary requests to get DOM.
            return [] 
        except Exception:
            return []

class GithubService:
    def __init__(self):
        self.token = settings.GITHUB_TOKEN
        self.base_url = "https://api.github.com/search/code"
        
    def search(self, query: str) -> List[Dict[str, Any]]:
        # GitHub search for 'filename:.env telegram_bot_token' etc.
        if not self.token:
            return []
            
        try:
            headers = {
                'Authorization': f'token {self.token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            params = {'q': query, 'per_page': 30}
            res = requests.get(self.base_url, headers=headers, params=params, timeout=15)
            res.raise_for_status()
            data = res.json()
            
            # We strictly can't get code content easily in bulk search without hitting limits.
            # We'll rely on user manually providing specific dorks that return raw url.
            # For this stub, we return empty list to avoid rate limits abusing.
            return []
        except Exception:
            return []
