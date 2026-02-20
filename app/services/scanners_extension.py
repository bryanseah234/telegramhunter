import httpx
import asyncio
from typing import List, Dict, Any
import logging
from app.core.config import settings

logger = logging.getLogger("scanners")

# (assuming _is_valid_token, TOKEN_PATTERN, and SPOOFED_HEADERS exist in scanners.py)

class GitlabService:
    def __init__(self):
        self.token = settings.GITLAB_TOKEN
        self.base_url = "https://gitlab.com/api/v4/search"
        
    async def search(self, query: str = "api.telegram.org/bot") -> List[Dict[str, Any]]:
        if not self.token:
            logger.warning("    [GitLab] Missing GITLAB_TOKEN")
            return []
        
        try:
            headers = {"PRIVATE-TOKEN": self.token}
            params = {"scope": "blobs", "search": query}
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.base_url, headers=headers, params=params)
                res.raise_for_status()
                items = res.json()
            
            # GitLab blobs api returns project_id and filename. 
            # We must fetch the raw blob or the file content via projects API.
            # Due to GitLab API structures, getting raw content is complex in search scopes.
            # Instead, we will simulate scraping the blob URL directly or doing a shallow parse of the match data.
            results = []
            
            async with httpx.AsyncClient(verify=False, timeout=10.0) as raw_client:
                tasks = []
                sem = asyncio.Semaphore(10)
                
                async def fetch_raw(item):
                    from app.services.scanners import TOKEN_PATTERN, _is_valid_token
                    async with sem:
                        project_id = item.get("project_id")
                        filename = item.get("filename")
                        ref = item.get("ref", "master")
                        
                        raw_url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/files/{filename}/raw?ref={ref}"
                        try:
                            raw_res = await raw_client.get(raw_url, headers=headers)
                            content = raw_res.text
                            found = TOKEN_PATTERN.findall(content)
                            local_res = []
                            for t in found:
                                if not _is_valid_token(t): continue
                                local_res.append({
                                    "token": t,
                                    "meta": {"source": "gitlab", "project_id": project_id, "file": filename}
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
            logger.error(f"    [GitLab] Error: {e}")
            return []


class GithubGistService:
    def __init__(self):
        self.token = settings.GITHUB_TOKEN
        self.base_url = "https://api.github.com/gists/public"
        
    async def search(self) -> List[Dict[str, Any]]:
        # Gists public endpoint shows recently created gists.
        # We fetch the recent 100 and look for tokens.
        if not self.token:
            logger.warning("    [Gist] Missing GITHUB_TOKEN")
            return []
        try:
            headers = {
                'Authorization': f'token {self.token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            params = {"per_page": 100}
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.base_url, headers=headers, params=params)
                res.raise_for_status()
                items = res.json()
            
            results = []
            async with httpx.AsyncClient(verify=False, timeout=10.0) as raw_client:
                tasks = []
                sem = asyncio.Semaphore(10)
                
                async def process_gist(gist):
                    from app.services.scanners import TOKEN_PATTERN, _is_valid_token
                    async with sem:
                        local_res = []
                        files = gist.get("files", {})
                        for filename, file_data in files.items():
                            raw_url = file_data.get("raw_url")
                            if raw_url:
                                try:
                                    raw_res = await raw_client.get(raw_url)
                                    content = raw_res.text
                                    found = TOKEN_PATTERN.findall(content)
                                    for t in found:
                                        if not _is_valid_token(t): continue
                                        local_res.append({
                                            "token": t,
                                            "meta": {"source": "gist", "gist_id": gist.get("id"), "file": filename}
                                        })
                                except Exception: pass
                        return local_res
                
                for item in items:
                    tasks.append(process_gist(item))
                
                scan_results = await asyncio.gather(*tasks, return_exceptions=True)
                for batch in scan_results:
                    if batch and isinstance(batch, list):
                        results.extend(batch)
                        
            return results

        except Exception as e:
            logger.error(f"    [Gist] Error: {e}")
            return []


class GrepAppService:
    def __init__(self):
        self.base_url = "https://grep.app/api/search"
        
    async def search(self) -> List[Dict[str, Any]]:
        # We can search using exactly the token regex!
        # \b(\d{8,10}:[A-Za-z0-9_-]{35})\b is too broad for grep.app sometimes,
        # but grep.app supports re2 regex. Let's search for telegram bot token patterns.
        query = r"api\.telegram\.org/bot\d{8,10}:[A-Za-z0-9_-]{35}"
        
        try:
            params = {"q": query, "regexp": "true"}
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.base_url, params=params)
                res.raise_for_status()
                data = res.json()
                
            results = []
            from app.services.scanners import TOKEN_PATTERN, _is_valid_token
            # Extracts from snippets
            hits = data.get("hits", {}).get("hits", [])
            for hit in hits:
                content = hit.get("content", {}).get("snippet", "")
                found = TOKEN_PATTERN.findall(content)
                for t in found:
                    if _is_valid_token(t):
                        results.append({
                            "token": t,
                            "meta": {"source": "grep.app", "repo": hit.get("repo")}
                        })
            return results
        except Exception as e:
            logger.error(f"    [grep.app] Error: {e}")
            return []


class PublicWwwService:
    def __init__(self):
        self.key = settings.PUBLICWWW_KEY
        self.base_url = "https://publicwww.com/websites/"
        
    async def search(self, query: str = '"api.telegram.org/bot"') -> List[Dict[str, Any]]:
        if not self.key:
            logger.warning("    [PublicWWW] Missing PUBLICWWW_KEY")
            return []
            
        try:
            # PublicWWW format: https://publicwww.com/websites/"query"/?export=csv&key=API_KEY
            url = f"{self.base_url}{query}/"
            params = {"export": "json", "key": self.key, "limit": 100}
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(url, params=params)
                if res.status_code == 403:
                    logger.warning("    [PublicWWW] Rate limit or Bad Key")
                    return []
                res.raise_for_status()
                # Returns list of domains
                domains = res.json()
                
            results = []
            async with httpx.AsyncClient(verify=False, timeout=10.0) as scan_client:
                tasks = []
                sem = asyncio.Semaphore(15)
                
                async def scan_domain(domain):
                    from app.services.scanners import _perform_active_deep_scan
                    async with sem:
                        target = f"http://{domain}" if not domain.startswith("http") else domain
                        try:
                            items = await _perform_active_deep_scan(target, client=scan_client)
                            return target, items
                        except Exception: return None
                
                for d in domains:
                    tasks.append(scan_domain(d))
                    
                scan_results = await asyncio.gather(*tasks, return_exceptions=True)
                for res_item in scan_results:
                    if not res_item or isinstance(res_item, Exception): continue
                    target_url, items = res_item
                    for t_item in items:
                        results.append({
                            "token": t_item['token'],
                            "chat_id": t_item.get('chat_id'),
                            "meta": {"source": "publicwww", "url": target_url}
                        })
            return results
        except Exception as e:
            logger.error(f"    [PublicWWW] Error: {e}")
            return []


class GoogleSearchService:
    def __init__(self):
        self.key = settings.GOOGLE_SEARCH_KEY
        self.cse_id = settings.GOOGLE_CSE_ID
        self.base_url = "https://www.googleapis.com/customsearch/v1"
        
    async def search(self, dork: str = r'site:pastebin.com "api.telegram.org/bot"') -> List[Dict[str, Any]]:
        if not self.key or not self.cse_id:
            logger.warning("    [GoogleSearch] Missing GOOGLE_SEARCH_KEY or GOOGLE_CSE_ID")
            return []
            
        try:
            params = {
                "key": self.key,
                "cx": self.cse_id,
                "q": dork,
                "num": 10
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.base_url, params=params)
                res.raise_for_status()
                data = res.json()
                
            results = []
            from app.services.scanners import _perform_active_deep_scan
            items = data.get("items", [])
            
            async with httpx.AsyncClient(verify=False, timeout=10.0) as scan_client:
                tasks = []
                sem = asyncio.Semaphore(5)
                
                async def scan_url(link):
                    async with sem:
                        try:
                            found = await _perform_active_deep_scan(link, client=scan_client)
                            return link, found
                        except Exception: return None
                
                for item in items:
                    tasks.append(scan_url(item.get("link")))
                    
                scan_results = await asyncio.gather(*tasks, return_exceptions=True)
                for res_item in scan_results:
                    if not res_item or isinstance(res_item, Exception): continue
                    target_url, f_items = res_item
                    for t_item in f_items:
                        results.append({
                            "token": t_item['token'],
                            "chat_id": t_item.get('chat_id'),
                            "meta": {"source": "google_dork", "url": target_url}
                        })
            return results
            
        except Exception as e:
            logger.error(f"    [GoogleSearch] Error: {e}")
            return []


class BitbucketService:
    def __init__(self):
        self.user = settings.BITBUCKET_USER
        self.password = settings.BITBUCKET_APP_PASSWORD
        self.base_url = "https://api.bitbucket.org/2.0/workspaces"
        
    async def search(self) -> List[Dict[str, Any]]:
        # Bitbucket search API requires a workspace in context, so a global search is hard.
        # As an alternative, we will scan recent public snippets
        if not self.user or not self.password:
            logger.warning("    [Bitbucket] Missing BITBUCKET credentials")
            return []
            
        snippet_url = "https://api.bitbucket.org/2.0/snippets"
        try:
            auth = (self.user, self.password)
            async with httpx.AsyncClient(timeout=30.0, auth=auth) as client:
                res = await client.get(snippet_url, params={"role": "member"}) # public snippets are hard to index, testing personal snippets or we skip.
                # Since Bitbucket global cross-repository search is disabled/deprecated for API,
                # we'll use a placeholder/simplified version grabbing accessible snippets.
                # Actually, skipping global Bitbucket search because of API limitations
                # is standard. We will just return empty for now unless targeted.
            return []
        except Exception as e:
            logger.error(f"    [Bitbucket] Error: {e}")
            return []


class PastebinService:
    def __init__(self):
        self.base_url = "https://scrape.pastebin.com/api_scraping.php"
        
    async def search(self) -> List[Dict[str, Any]]:
        # Pastebin scraping API requires IP whitelist. If it fails, we return []
        try:
            params = {"limit": 100}
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(self.base_url, params=params)
                if res.status_code == 403:
                    logger.warning("    [Pastebin] IP not whitelisted for scraping API")
                    return []
                res.raise_for_status()
                pastes = res.json()
            
            results = []
            async with httpx.AsyncClient(verify=False, timeout=10.0) as raw_client:
                tasks = []
                sem = asyncio.Semaphore(15)
                
                async def fetch_paste(p):
                    from app.services.scanners import TOKEN_PATTERN, _is_valid_token
                    async with sem:
                        scrape_url = p.get("scrape_url")
                        try:
                            raw_res = await raw_client.get(scrape_url)
                            content = raw_res.text
                            found = TOKEN_PATTERN.findall(content)
                            local_res = []
                            for t in found:
                                if not _is_valid_token(t): continue
                                local_res.append({
                                    "token": t,
                                    "meta": {"source": "pastebin", "paste_key": p.get("key")}
                                })
                            return local_res
                        except Exception: return []

                for p in pastes:
                    tasks.append(fetch_paste(p))
                    
                scan_results = await asyncio.gather(*tasks, return_exceptions=True)
                for batch in scan_results:
                    if batch and isinstance(batch, list):
                        results.extend(batch)
                        
            return results
        except Exception as e:
             logger.error(f"    [Pastebin] Error: {e}")
             return []
