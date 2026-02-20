import asyncio
import httpx
import os
import sys

# Load env manually
env_vars = {}
with open(r'c:\telegramhunter\.env', 'r') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            env_vars[k] = v

async def test_gitlab():
    token = env_vars.get('GITLAB_TOKEN')
    print("Testing GitLab...")
    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://gitlab.com/api/v4/search?scope=blobs&search=api.telegram.org/bot",
            headers={"PRIVATE-TOKEN": token}
        )
        print(f"GitLab Status: {res.status_code}")
        print(f"GitLab Response: {res.text[:500]}")

async def test_publicwww():
    key = env_vars.get('PUBLICWWW_KEY')
    print("\nTesting PublicWWW...")
    async with httpx.AsyncClient() as client:
        url = f"https://publicwww.com/websites/\"api.telegram.org/bot\"/?export=json&key={key}&limit=10"
        res = await client.get(url)
        print(f"PublicWWW Status: {res.status_code}")
        print(f"PublicWWW Response: {res.text[:500]}")

async def test_google():
    key = env_vars.get('GOOGLE_SEARCH_KEY')
    cx = env_vars.get('GOOGLE_CSE_ID')
    print("\nTesting Google...")
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://www.googleapis.com/customsearch/v1?key={key}&cx={cx}&q=site:pastebin.com"
        )
        print(f"Google Status: {res.status_code}")
        print(f"Google Response: {res.text[:500]}")

async def main():
    await test_gitlab()
    await test_publicwww()
    await test_google()

if __name__ == "__main__":
    asyncio.run(main())
