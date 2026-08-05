# Cloudflare WAF Rules for winnethepooh.hong-yi.me
#
# Apply these in Cloudflare Dashboard → hong-yi.me → Security → WAF → Custom Rules
# Free tier: 5 custom rules. Here are the recommended 5.
#
# ============================================================
# RULE 1: Block all traffic that isn't Telegram webhook or monitor-key auth
# ============================================================
# Expression:
#   (http.host eq "winnethepooh.hong-yi.me")
#   and not (
#     (http.request.uri.path contains "/honeypot/receive" and http.request.method eq "POST")
#     or (any(http.request.headers["x-monitor-key"][*] ne ""))
#     or (http.request.uri.path eq "/" and http.request.method eq "GET")
#     or (http.request.uri.path eq "/health/" and http.request.method eq "GET")
#   )
# Action: BLOCK
# Priority: 1
#
# This ensures only:
#   - Telegram webhook POSTs to /honeypot/receive/* (our honeypot)
#   - Requests with X-Monitor-Key header (our monitoring)
#   - Root / and /health/ for uptime checks
# Everything else → blocked. No enumeration possible.
#
# ============================================================
# RULE 2: Rate limit honeypot receiver (anti-flood)
# ============================================================
# Expression:
#   (http.host eq "winnethepooh.hong-yi.me")
#   and (http.request.uri.path contains "/honeypot/receive")
#   and (http.request.method eq "POST")
# Action: Rate Limit → Block for 60s
# Rate: 100 requests per 10 seconds (per IP)
# Priority: 2
#
# Telegram sends at most ~30 updates/sec for a busy bot.
# 100/10s gives headroom while blocking abuse.
#
# ============================================================
# RULE 3: Block known bot scanners (User-Agent fingerprint)
# ============================================================
# Expression:
#   (http.host eq "winnethepooh.hong-yi.me")
#   and (
#     http.user_agent contains "nmap"
#     or http.user_agent contains "masscan"
#     or http.user_agent contains "ZmEu"
#     or http.user_agent contains "Nikto"
#     or http.user_agent contains "sqlmap"
#     or http.user_agent contains "gobuster"
#     or http.user_agent contains "dirbuster"
#     or http.user_agent contains "nuclei"
#     or http.user_agent contains "httpx"
#     or http.user_agent eq ""
#   )
# Action: BLOCK
# Priority: 3
#
# Empty UA = most automated scanners. Telegram's webhook POST uses
# a standard UA, never empty.
#
# ============================================================
# RULE 4: Challenge suspicious paths (directory traversal / admin probe)
# ============================================================
# Expression:
#   (http.host eq "winnethepooh.hong-yi.me")
#   and (
#     http.request.uri.path contains ".."
#     or http.request.uri.path contains "/admin"
#     or http.request.uri.path contains "/wp-"
#     or http.request.uri.path contains "/.env"
#     or http.request.uri.path contains "/.git"
#     or http.request.uri.path contains "/phpmyadmin"
#     or http.request.uri.path contains "/actuator"
#   )
# Action: MANAGED CHALLENGE (JS challenge)
# Priority: 4
#
# Bots fail JS challenges. Legit Telegram webhook POSTs hit /honeypot/receive
# which is already allowed by Rule 1.
#
# ============================================================
# RULE 5: Geo-restrict to Telegram's IP ranges (optional, aggressive)
# ============================================================
# Expression:
#   (http.host eq "winnethepooh.hong-yi.me")
#   and (http.request.uri.path contains "/honeypot/receive")
#   and not (ip.src in {149.154.160.0/20 91.108.4.0/22 91.108.8.0/22
#             91.108.12.0/22 91.108.16.0/22 91.108.20.0/22 91.108.56.0/22
#             91.108.36.0/23 185.76.151.0/24})
# Action: BLOCK
# Priority: 5
#
# These are Telegram's webhook source IP ranges (documented at
# https://core.telegram.org/bots/webhooks#the-short-version).
# AGGRESSIVE: may break if Telegram adds new ranges. Monitor logs.
# Alternative: use Managed Challenge instead of Block for softer enforcement.

# ============================================================
# HOW TO APPLY:
# 1. Go to https://dash.cloudflare.com → hong-yi.me → Security → WAF
# 2. Click "Create rule" for each of the 5 rules above
# 3. Copy the Expression into the expression builder (switch to "Edit expression")
# 4. Set the Action as noted
# 5. Save + Deploy each rule
#
# Total time: ~5 minutes clicking through the dashboard.
# ============================================================
