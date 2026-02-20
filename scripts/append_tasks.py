import os

file_path = r'c:\telegramhunter\app\workers\tasks\scanner_tasks.py'

# 1. Update Imports at the top of the file
new_imports = "from app.services.scanners import GitlabService, BitbucketService, GithubGistService, GrepAppService, PublicWwwService, PastebinService, GoogleSearchService\n"
new_instantiations = """
gitlab_srv = GitlabService()
bitbucket_srv = BitbucketService()
gist_srv = GithubGistService()
grepapp_srv = GrepAppService()
publicwww_srv = PublicWwwService()
pastebin_srv = PastebinService()
google_search_srv = GoogleSearchService()
"""

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Insert after existing imports
import_target = "from app.services.scanners import ShodanService, GithubService, UrlScanService, FofaService"
if import_target in content:
    content = content.replace(import_target, f"{import_target}\n{new_imports}")

# Insert instantiations
inst_target = "fofa = FofaService()"
if inst_target in content:
    content = content.replace(inst_target, f"{inst_target}\n{new_instantiations}")

# 2. Append 7 new tasks at the end
# I'll create a factory string for them to save space here.
task_template = """
@app.task(name="scanner.scan_{name}")
def scan_{name}(query: str = None):
    return _run_sync(_scan_{name}_async(query))

async def _scan_{name}_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [{name_title}] System is PAUSED. Skipping scan.")
        return "System Paused"

    logger.info("🔍 [{name_title}] Starting scan...")
    await _send_log_async("🔍 [{name_title}] Starting scheduled scan...")
    
    total_saved = 0
    errors = []
    
    try:
        results = await {service_var}.search()
        logger.info(f"    ✅ [{name_title}] Returned {{len(results)}} matches.")
        saved = await _save_credentials_async(results, "{name}")
        total_saved += saved
    except Exception as e:
        logger.error(f"    ❌ [{name_title}] Scan failed: {{str(e)}}")
        errors.append(str(e))
        
    result_msg = f"{name_title} scan finished. Saved {{total_saved}} new credentials."
    if errors:
         result_msg += f" (Errors: {{len(errors)}})"
         await _send_log_async(f"❌ [{name_title}] Completed with errors: {{errors[0]}}...")
    else:
         await _send_log_async(f"🏁 [{name_title}] Finished. Saved {{total_saved}} new credentials.")
         
    return result_msg
"""

# Add google dork override which takes a query
google_task = """
@app.task(name="scanner.scan_googledork")
def scan_googledork(query: str = None):
    return _run_sync(_scan_googledork_async(query))

async def _scan_googledork_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
         return "System Paused"

    # Default clones to sweep
    dorks = [
        'site:pastebin.com "api.telegram.org/bot"',
        'site:hastebin.com "api.telegram.org/bot"',
        'site:ghostbin.com "api.telegram.org/bot"',
        'site:rentry.co "api.telegram.org/bot"',
    ]
    if query:
        dorks = [query]

    logger.info(f"🔍 [GoogleDork] Starting scan with {len(dorks)} dorks...")
    await _send_log_async(f"🔍 [GoogleDork] Starting sweep across {len(dorks)} paste sites...")
    
    total_saved = 0
    for dork in dorks:
        try:
            results = await google_search_srv.search(dork)
            saved = await _save_credentials_async(results, "google_dork")
            total_saved += saved
        except Exception as e:
            logger.error(f"    ❌ [GoogleDork] Failed on {dork}: {e}")
            
    await _send_log_async(f"🏁 [GoogleDork] Finished. Saved {total_saved} new credentials.")
    return f"GoogleDork scan finished. Saved {total_saved}."
"""


content += task_template.format(name="gitlab", name_title="GitLab", service_var="gitlab_srv")
content += task_template.format(name="bitbucket", name_title="Bitbucket", service_var="bitbucket_srv")
content += task_template.format(name="gist", name_title="Gist", service_var="gist_srv")
content += task_template.format(name="grepapp", name_title="GrepApp", service_var="grepapp_srv")
content += task_template.format(name="publicwww", name_title="PublicWWW", service_var="publicwww_srv")
content += task_template.format(name="pastebin", name_title="Pastebin", service_var="pastebin_srv")
content += google_task

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Tasks appended successfully.")
