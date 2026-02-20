import asyncio
import httpx
import os
import sys

# Load env manually to ensure we see exactly what is there
env_vars = {}
try:
    with open(r'c:\telegramhunter\.env', 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env_vars[k.strip()] = v.strip()
except Exception as e:
    print(f"Error reading .env: {e}")
    sys.exit(1)

async def check_github():
    token = env_vars.get('GITHUB_TOKEN')
    if not token: return "🔴 Missing"
    async with httpx.AsyncClient() as client:
        res = await client.get("https://api.github.com/user", headers={"Authorization": f"token {token}"})
        if res.status_code == 200: return "✅ Valid"
        return f"❌ Invalid ({res.status_code})"

async def check_shodan():
    key = env_vars.get('SHODAN_KEY')
    if not key: return "🔴 Missing"
    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://api.shodan.io/api-info?key={key}")
        if res.status_code == 200: return "✅ Valid"
        return f"❌ Invalid ({res.status_code})"

async def check_fofa():
    email = env_vars.get('FOFA_EMAIL')
    key = env_vars.get('FOFA_KEY')
    if not email or not key: return "🔴 Missing"
    import base64
    qbase64 = base64.b64encode('domain="google.com"'.encode()).decode()
    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://fofa.info/api/v1/search/all?email={email}&key={key}&qbase64={qbase64}&size=1")
        if res.status_code == 200:
            if res.json().get('error') == False: return "✅ Valid"
            return f"❌ FOFA Error: {res.json().get('errmsg')}"
        return f"❌ Invalid ({res.status_code})"

async def check_urlscan():
    key = env_vars.get('URLSCAN_KEY')
    if not key: return "🔴 Missing"
    async with httpx.AsyncClient() as client:
        res = await client.get("https://urlscan.io/api/v1/search/?q=domain:google.com&size=1", headers={"API-Key": key})
        if res.status_code == 200: return "✅ Valid"
        return f"❌ Invalid ({res.status_code})"

async def check_gitlab():
    token = env_vars.get('GITLAB_TOKEN')
    if not token: return "🔴 Missing"
    async with httpx.AsyncClient() as client:
        res = await client.get("https://gitlab.com/api/v4/user", headers={"PRIVATE-TOKEN": token})
        if res.status_code == 200: return "✅ Valid"
        return f"❌ Invalid/Scopes Missing ({res.status_code})"

async def check_publicwww():
    key = env_vars.get('PUBLICWWW_KEY')
    if not key: return "🔴 Missing"
    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://publicwww.com/websites/google/?export=json&key={key}&limit=1")
        if "API available for paid search results only" in res.text: return "❌ Requires Paid Plan"
        if res.status_code == 200: return "✅ Valid"
        return f"❌ Invalid ({res.status_code})"

async def check_serper():
    key = env_vars.get('SERPER_API_KEY')
    if not key: return "🔴 Missing"
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": "test", "num": 1}
        )
        if res.status_code == 200: return "✅ Valid"
        data = res.json()
        err_msg = data.get('message', 'Unknown Error')
        return f"❌ {err_msg} ({res.status_code})"

async def main():
    print(f"{'Service':<20} | {'Status'}")
    print("-" * 40)
    results = await asyncio.gather(
        check_github(), check_shodan(), check_fofa(), check_urlscan(), 
        check_gitlab(), check_publicwww(), check_serper()
    )
    services = ["GitHub", "Shodan", "FOFA", "URLScan", "GitLab", "PublicWWW", "Serper.dev"]
    for s, r in zip(services, results):
        print(f"{s:<20} | {r}")

if __name__ == "__main__":
    asyncio.run(main())
