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
    countryIndex: 0,
    countriesDone: 0,
    resultsFound: 0,
    results: [],     // Array of {token, chat_id}
    seenTokens: new Set() // For dedup
};

let activeTabId = null;

// --- LISTENERS ---
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    switch (msg.action) {
        case "GET_STATE":
            sendResponse(state);
            break;
        case "START_SCAN":
            startScan(msg.query);
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
});

// --- CORE LOGIC ---

async function startScan(userQuery) {
    if (state.isRunning) return;

    // Reset State
    state.isRunning = true;
    state.isPaused = false;
    state.status = "Starting...";
    state.query = userQuery || BASE_QUERY_TEMPLATE;
    state.countryIndex = 0;
    state.countriesDone = 0;
    state.resultsFound = 0;
    state.results = [];
    state.seenTokens = new Set();

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
    broadcastState();
}

function pauseScan(reason) {
    state.isPaused = true;
    state.status = reason || "Paused";
    broadcastState();
}

function resumeScan() {
    if (!state.isRunning) return;
    state.isPaused = false;
    state.status = "Resuming...";
    broadcastState();

    // Tell content script to retry/continue? 
    // Or just re-trigger the check?
    // If we were paused on a captcha, user solved it. 
    // We should reload the page or re-inject logic?
    // Let's safe-bet: Just tell content script to "resume" if it has logic, 
    // or arguably just re-process the current country.

    chrome.tabs.sendMessage(activeTabId, { action: "RESUME_WORK" }).catch(() => {
        // If content script is dead (e.g. page reloaded), reload page
        processNextCountry(false); // don't increment index
    });
}

function nextCountry() {
    state.countryIndex++;
    state.countriesDone++;
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
    state.status = `Navigating: ${country}`;
    broadcastState();

    const fullQuery = `${state.query} && country="${country}"`;
    const encoded = btoa(fullQuery);
    const targetUrl = `https://fofa.info/result?qbase64=${encoded}`;

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


function handleResult(data) {
    if (state.seenTokens.has(data.token)) return;

    state.seenTokens.add(data.token);
    state.results.push(data);
    state.resultsFound++;
    broadcastState();
}

function broadcastState() {
    chrome.runtime.sendMessage({ action: "STATE_UPDATE", state: state }).catch(() => { });
}

function downloadResults() {
    // Generate CSV Blob
    let csvContent = "token,chat_id\n";
    state.results.forEach(row => {
        csvContent += `${row.token},${row.chatId}\n`;
    });

    // In background script, we can use Data URI download
    const date = new Date().toISOString().slice(0, 19).replace(/:/g, "-");
    const filename = `telegram_hunter_${date}.csv`;

    // Create a data URL
    const base64 = btoa(unescape(encodeURIComponent(csvContent)));
    const url = 'data:text/csv;base64,' + base64;

    chrome.downloads.download({
        url: url,
        filename: filename,
        saveAs: true
    });
}
