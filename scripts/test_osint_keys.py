import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.scanners import (
    GitlabService, 
    BitbucketService, 
    GithubGistService, 
    GrepAppService, 
    PublicWwwService, 
    PastebinService, 
    GoogleSearchService
)

async def test_all():
    print("=== Testing OSINT API Keys ===")
    
    services = [
        ("GitLab", GitlabService()),
        ("Bitbucket", BitbucketService()),
        ("GitHub Gist", GithubGistService()),
        ("Grep.app", GrepAppService()),
        ("PublicWWW", PublicWwwService()),
        ("Pastebin", PastebinService()),
        ("Google CSE (Dork)", GoogleSearchService())
    ]
    
    for name, srv in services:
        print(f"\n[*] Testing {name}...")
        try:
            # We'll put a timeout to ensure it doesn't hang testing
            res = await asyncio.wait_for(srv.search(), timeout=45.0)
            status = "✅ SUCCESS"
            if res == [] and getattr(srv, 'key', None) or getattr(srv, 'token', None):
                 # Some return empty list on bad auth, so maybe check config
                 if not getattr(srv, 'token', True) and not getattr(srv, 'key', True) and not getattr(srv, 'user', True):
                      status = "⚠️ MISSING KEY"
            elif res == []:
                 status = "✅ SUCCESS (0 results found, but no errors)"
                 
            print(f"    {status} -> Returned {len(res)} items.")
        except asyncio.TimeoutError:
            print(f"    ❌ TIMEOUT: The service took too long to respond.")
        except Exception as e:
            print(f"    ❌ FAILED: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_all())
