# Phase 2: Zero-Cost Hit Rate Expansion

This document details the architecture and tasks for Phase 2 of the TelegramHunter hit rate expansion. All data sources detailed below rely on public, unauthenticated, or free-tier APIs ($0 cost).

## 1. GitHub Firehose Expansion (Issues & PRs)
**Goal:** Developers frequently leak `.env` variables or console logs into GitHub Issues and Pull Requests when asking for help debugging.
**Architecture:** 
- The existing `firehose.poll_github_events` task polls `api.github.com/events` every 30s.
- It currently only processes `PushEvent` (code commits). 
- We will expand it to process `IssuesEvent`, `IssueCommentEvent`, and `PullRequestEvent`.
- **Cost:** $0. Uses the existing authenticated GitHub token pool (5,000 req/hr). Furthermore, the text payloads for Issues/Comments are included *directly in the event payload*, meaning we don't even need to make secondary HTTP requests to fetch diffs like we do for commits.

## 2. Docker Hub Manifest Scraping
**Goal:** Catch bot tokens hardcoded as `ENV TELEGRAM_TOKEN=xxx` inside public Dockerfiles.
**Architecture:**
- Create a new task `scanner.scan_dockerhub` that runs hourly.
- Use the free Docker Hub Search API: `GET https://hub.docker.com/v2/search/repositories?query=telegram+bot`
- For each image, fetch the manifest and the configuration blob using the public Docker Registry API.
- Search the `config.Env` array for the `TOKEN_PATTERN`.
- **Cost:** $0. Docker Registry APIs are free to read. Unauthenticated manifest pulls have a rate limit (100 pulls / 6 hours per IP), which is plenty if we pace our scanner to only check new/recently updated images.

## 3. Active Honeypot (DNS Sinkhole / Webhook Trap)
**Goal:** Catch malicious C2 frameworks and sloppy bot devs who mistype the Telegram API URL.
**Security & IP Protection:** 
- **Your Home IP will NEVER be exposed.**
- We will deploy a **Cloudflare Tunnel (cloudflared)** Docker container alongside your stack.
- The tunnel creates a secure outbound connection from your machine to Cloudflare's edge network. Cloudflare assigns you a free public domain (or you can use your own).
- Attackers hit Cloudflare's IP addresses, not yours. Cloudflare securely forwards the traffic down the tunnel to a new FastAPI router (`/honeypot/{path:path}`) in your `api` container.
- If the request looks like a Telegram Bot API call (`POST /bot<TOKEN>/sendMessage`), we silently extract the token, drop it into the `discovered_credentials` table, and return a fake `HTTP 200 OK {"ok": true}` to trick the malware into continuing.
- **Cost:** $0. Cloudflare Tunnels (Zero Trust) are completely free.

## 4. Pastebin Alternatives (Rentry & Hastebin)
**Goal:** Bypass Pastebin's IP blocks by targeting modern paste sites favored by threat actors.
**Architecture:**
- Create `scanner.scan_rentry` and `scanner.scan_hastebin`.
- Utilize public search engines (via existing `ExaService` or `GoogleSearchService`) using dorks like `site:rentry.co "api.telegram.org/bot"`.
- Fetch the raw paste text (e.g., `https://rentry.co/api/raw/{id}`).
- **Cost:** $0. Uses existing search quotas and free raw text endpoints.

---

## Execution Checklist

- [ ] **Bundle 1: Firehose Expansion** (Modify `firehose_tasks.py` to extract Issue/PR bodies)
- [ ] **Bundle 2: Pastebin Alternatives** (Add Rentry/Hastebin parsers to `scanners_extension.py`)
- [ ] **Bundle 3: Docker Hub Scanner** (Add registry pull logic to `scanner_tasks.py`)
- [ ] **Bundle 4: Active Honeypot** (Add `cloudflared` to compose, build fake API routes)
