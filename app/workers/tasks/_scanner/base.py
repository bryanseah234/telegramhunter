"""
Generic scanner coroutine — extracted from scanner_tasks.py.

_run_scanner() covers three structural patterns:
  A. Single-call scanners: service_factory() returns a service, call .search()
     once, pass results to _save_credentials_async.
  B. Query-loop scanners: iterate queries, call search(q) for each.
  C. Query-loop + cooldown gate: same as B but gate on a Redis cooldown key
     before entering the loop.

Bespoke scanners (retry_cold, telegram_search, scan_github) stay in
scanner_tasks.py — they are structurally too different to fit this base.
"""
import logging
import os

logger = logging.getLogger("scanner.base")


async def _run_scanner(
    *,
    label: str,
    search_fn,
    source_name: str,
    save_fn,
    send_log_fn,
    redis_client,
    queries: list = None,
    cooldown_key: str = None,
    cooldown_env: str = None,
    cooldown_default: int = 82800,
    budget_check_fn=None,
) -> str:
    """
    Generic scanner coroutine.

    Args:
        label:           Display name for logs, e.g. "Shodan".
        search_fn:       Async callable. Called as:
                           - search_fn(q) if queries provided (query-loop)
                           - search_fn()  if queries is None (single-call)
        source_name:     String passed to _save_credentials_async.
        save_fn:         _save_credentials_async reference.
        send_log_fn:     _send_log_async reference.
        redis_client:    Redis client for pause + cooldown checks.
        queries:         List of query strings. None = single-call mode.
        cooldown_key:    Redis key for broken-API cooldown. None = no gate.
        cooldown_env:    Env var name for cooldown TTL override. None = use default.
        cooldown_default: Default cooldown TTL in seconds (default 82800 = 23h).
        budget_check_fn: Optional async callable() -> int remaining budget.
                         If provided and returns 0, scan is skipped.
    """
    # ── Pause gate ────────────────────────────────────────────────────────────
    if redis_client.get("system:paused"):
        logger.warning(f"[{label}] System is PAUSED. Skipping scan.")
        return "System Paused"

    # ── Cooldown gate (broken API key) ────────────────────────────────────────
    if cooldown_key and redis_client.get(cooldown_key):
        ttl_val = redis_client.ttl(cooldown_key)
        logger.info(f"[{label}] API key on cooldown ({ttl_val}s remaining) -- skipping.")
        return f"{label} API key on cooldown -- skipped."

    # ── Budget gate (e.g. Netlas daily limit) ─────────────────────────────────
    if budget_check_fn is not None:
        remaining = await budget_check_fn()
        if remaining == 0:
            msg = f"[{label}] Daily budget exhausted -- skipping."
            logger.warning(msg)
            await send_log_fn(msg)
            return "Daily limit reached"

    total_saved = 0
    errors: list[str] = []

    if queries is None:
        # ── Pattern A: single-call ─────────────────────────────────────────
        logger.info(f"[{label}] Starting scan...")
        await send_log_fn(f"[{label}] Starting scheduled scan...")
        try:
            results = await search_fn()
            logger.info(f"    [{label}] Returned {len(results)} matches.")
            saved = await save_fn(results, source_name)
            total_saved += saved
        except Exception as e:
            logger.error(f"    [{label}] Scan failed: {e}")
            errors.append(str(e))
    else:
        # ── Pattern B/C: query-loop ────────────────────────────────────────
        logger.info(f"[{label}] Starting scan | Queries: {len(queries)}")
        await send_log_fn(f"[{label}] Starting scan with {len(queries)} queries...")
        for q in queries:
            try:
                logger.info(f"    [{label}] Query: {q}")
                results = await search_fn(q)
                logger.info(f"    [{label}] Query returned {len(results)} matches.")
                saved = await save_fn(results, source_name)
                total_saved += saved
            except Exception as e:
                err = str(e)
                logger.error(f"    [{label}] Query failed: {err}")
                errors.append(err)
                # If a cooldown key is configured, set it on first 403/auth failure
                if cooldown_key and any(x in err for x in ("403", "401", "Unauthorized", "Forbidden")):
                    ttl = int(os.getenv(cooldown_env, cooldown_default)) if cooldown_env else cooldown_default
                    redis_client.setex(cooldown_key, ttl, "1")
                    logger.warning(f"    [{label}] Auth failure detected -- setting cooldown {ttl}s.")
                    break

    result_msg = f"{label} scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        await send_log_fn(f"[{label}] Completed with errors: {errors[0][:100]}")
    else:
        await send_log_fn(f"[{label}] Finished. Saved {total_saved} new credentials.")

    return result_msg
