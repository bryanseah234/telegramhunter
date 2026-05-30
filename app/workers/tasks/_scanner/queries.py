"""
Scanner query constants — extracted from scanner_tasks.py.

Public API (re-exported via scanner_tasks.py):
    _shodan_body_query(anchor, extra)
    SHODAN_DEFAULT_QUERIES
    FOFA_DEFAULT_QUERIES
    NETLAS_QUERIES
"""

def _shodan_body_query(anchor: str, extra: str = "") -> str:
    """Build a Shodan body query with standard exclusion filters and status check.

    Centralises the exclusion-filter suffix so it cannot be omitted from new entries.
    Every query produced by this helper contains both exclusion strings and http.status:200.
    """
    parts = [anchor]
    if extra:
        parts.append(extra)
    parts += ['-http.body:"telegram.org"', '-http.body:"github.com"', "http.status:200"]
    return " ".join(parts)


# ── Shodan default query list ─────────────────────────────────────────────────
# Replaces the inline default_queries construction in _scan_shodan_async.
# Ordered by tier: Tier 1 (standalone fingerprints), Tier 2 (C2 payload variants),
# Tier 3 (malware keywords), then legacy http.html:/http.title: entries.
SHODAN_DEFAULT_QUERIES = [
    # ── Tier 1: Standalone Telegram fingerprint queries ───────────────────
    'http.headers:"X-Telegram-Bot-Api"',
    'http.body:"api.telegram.org/bot" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"http://t.me/bot" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"https://t.me" http.body:"/start" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    # ── Tier 2: C2 payload queries (anchored to api.telegram.org/bot) ─────
    'http.body:"api.telegram.org/bot" http.body:"/start payload=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start token=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start cmd=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start c2=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start key=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start id=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /bin/bash" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /powershell" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start download" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /exec" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /run" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /invoke" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /script" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start http://" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start https://" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    # ── Tier 3: Malware keyword queries (anchored to api.telegram.org/bot) ─
    'http.body:"api.telegram.org/bot" http.body:"malware" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"rat" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"remote access" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"spyware" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"stealer" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"keylogger" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"c2 server" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"command and control" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"exploit" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"bypass" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"inject" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"persistence" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"privilege escalation" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    # ── Legacy http.html: / http.title: entries (retained unchanged) ──────
    'http.html:"api.telegram.org/bot"',
    'http.html:"bot_token"',
    'http.html:"TELEGRAM_BOT_TOKEN"',
    'http.html:"TELEGRAM_TOKEN"',
    'http.html:"Telegram Bot"',
    'http.html:"https://api.telegram.org"',
    'http.title:"Telegram Bot"',
    'http.title:"Telegram Login"',
]


# ── FOFA default query list ───────────────────────────────────────────────────
# Replaces the inline COMMON_QUERIES construction in _scan_fofa_async.
# Ordered by tier: existing entries first, then Tier 1 t.me, Tier 2 C2 payloads, Tier 3 malware.
# FOFA does not support negation in the same way as Shodan/Netlas; exclusions are omitted.
FOFA_DEFAULT_QUERIES = [
    # ── Tier 1: Direct structural matches (highest yield) ─────────────────
    # These anchor on the actual API path, not keyword soup.
    'body="api.telegram.org/bot" && status_code="200"',
    'body="TELEGRAM_BOT_TOKEN" && status_code="200"',
    'body="bot_token" && status_code="200"',
    # ── Tier 2: Bot UI fingerprints ────────────────────────────────────────
    'title="Telegram Bot" && status_code="200"',
    'body="sendMessage" && body="chat_id" && status_code="200"',
    # ── Tier 3: t.me URL patterns ──────────────────────────────────────────
    'body="http://t.me/bot" && status_code="200"',
    'body="https://t.me" && body="/start" && status_code="200"',
    # ── Removed ────────────────────────────────────────────────────────────
    # All 13 Tier-2/3 C2 keyword queries (c2 server, exploit, bypass, inject,
    # persistence, malware, rat, spyware, stealer, keylogger, remote access,
    # command and control, privilege escalation) returned 0 results across
    # every run for 7+ hours. FOFA does not index live C2 page body content
    # against these terms with status_code=200. Removed to cut cycle from
    # ~84s (30 queries × 2s) down to ~14s (7 queries × 2s).
    # Re-add if you want to experiment: each costs ~2s + FOFA API quota.
]


# ── Shared query bank ─────────────────────────────────────────────────────────
# All Netlas queries live here. Ordered by expected yield (highest first).
# Each query costs 1 search coin. With 100 req/day across both accounts,
# we run the top N queries that fit within the remaining daily budget.
NETLAS_QUERIES = [
    # ── Direct token in HTTP body (highest yield) ─────────────────────────
    'http.body:"api.telegram.org/bot" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"TELEGRAM_BOT_TOKEN" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"bot_token" http.body:"api.telegram.org" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"TG_BOT_TOKEN" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    # ── Telegram header fingerprint ───────────────────────────────────────
    'http.headers:"X-Telegram-Bot-Api"',
    # ── C2 / RAT / Malware bots ───────────────────────────────────────────
    'http.body:"api.telegram.org/bot" http.body:"malware" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"rat" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"stealer" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"keylogger" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"c2" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"remote access" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"exploit" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"inject" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"persistence" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"privilege escalation" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"spyware" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"bypass" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"command and control" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    # ── /start command C2 patterns ────────────────────────────────────────
    'http.body:"api.telegram.org/bot" http.body:"/start payload=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start token=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start cmd=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start c2=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start key=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start id=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /bin/bash" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /powershell" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start download" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /exec" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /run" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /invoke" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /script" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start http://" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start https://" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    # ── Config file patterns exposed on web ───────────────────────────────
    'http.body:"TELEGRAM_BOT_TOKEN" http.body:"REDIS_URL" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"TELEGRAM_BOT_TOKEN" http.body:"DATABASE_URL" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"bot_token" http.body:"webhook" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    # ── Telegram t.me patterns ────────────────────────────────────────────
    'http.body:"https://t.me" http.body:"/start" http.body:"api.telegram.org" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"http://t.me/bot" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
]


