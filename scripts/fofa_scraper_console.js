(async function () {
    // --- QUERY CONFIGURATION ---
    const BASE_QUERY = 'body="api.telegram.org"';

    // --- COUNTRY LIST ---
    const COUNTRY_CODES = [
        "US", "CN", "HK", "RU", "FR", "DE", "NL", "SG", "GB", "JP",
        "KR", "IN", "BR", "CA", "AU", "IT", "ES", "TR", "UA", "VN",
        "ID", "PL", "SE", "CH", "NO", "FI", "DK", "IE", "AT", "CZ",
        "RO", "ZA", "MX", "AR", "CO", "CL", "MY", "TH", "PH", "PK",
        "IR", "SA", "AE", "IL", "GR", "PT", "BE", "HU", "NZ"
    ];

    // --- DOM CONFIG (Worker Window) ---
    const CLICK_SELECTOR = '.el-tooltip.iconfont.icon-daima';
    const POPUP_IFRAME_SELECTOR = '.el-dialog__body iframe';

    // STORAGE & TIMERS
    const STORAGE_KEY_SEEN = 'tg_hunter_seen_tokens';
    const MAX_WAIT_MS = 10000;
    const CHECK_INTERVAL_MS = 500;
    const CLOSE_WAIT_MS = 800;
    const PAGE_LOAD_WAIT_MS = 6000; // Time for Worker window to load new URL

    // STATE
    let isRunning = true;
    let isPaused = false;
    let stats = { countries: 0, scanned: 0, found: 0, duplicates: 0 };
    let results = [];
    let workerWindow = null;

    // --- ENCODING HELPER ---
    function encodeQuery(query) {
        return btoa(query);
    }

    // --- UI OVERLAY (On Main/Controller Window) ---
    const ui = document.createElement('div');
    ui.id = 'th-controller-ui';
    ui.style.cssText = `
        position: fixed; bottom: 20px; right: 20px; width: 340px;
        background: rgba(0, 0, 0, 0.95); color: #0f0; border: 2px solid #0f0;
        padding: 15px; font-family: monospace; z-index: 999999;
        box-shadow: 0 0 20px rgba(0, 255, 0, 0.3); border-radius: 8px;
        font-size: 13px;
    `;
    ui.innerHTML = `
        <h3 style="margin: 0 0 10px; color: #fff; border-bottom: 1px solid #333; padding-bottom: 5px; display:flex; justify-content:space-between;">
            <span>🎮 TH Controller</span>
            <span id="th-state" style="color: #0f0;">RUNNING</span>
        </h3>
        <div id="th-status" style="margin-bottom: 10px; color: #ff0; font-weight:bold;">Waiting to start...</div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; color: #fff;">
            <div>🌎 <span id="th-pages">0</span>/${COUNTRY_CODES.length}</div>
            <div>💎 Found: <span id="th-found">0</span></div>
            <div>🔍 Scan: <span id="th-scanned">0</span></div>
            <div>♻️ Skip: <span id="th-dupes">0</span></div>
        </div>
        <div style="display: flex; gap: 10px;">
            <button id="th-pause" style="
                flex: 1; background: #fb0; color: #000; border: none; 
                padding: 10px; cursor: pointer; font-weight: bold; border-radius: 4px; text-transform:uppercase;">
                PAUSE
            </button>
            <button id="th-stop" style="
                flex: 1; background: #c00; color: #fff; border: none; 
                padding: 10px; cursor: pointer; font-weight: bold; border-radius: 4px; text-transform:uppercase;">
                STOP
            </button>
        </div>
    `;

    // Remove old UI if exists
    const oldUI = document.getElementById('th-controller-ui');
    if (oldUI) oldUI.remove();
    document.body.appendChild(ui);

    document.getElementById('th-stop').onclick = () => {
        isRunning = false;
        isPaused = false; // Break out of pause loop
        updateStatus("🛑 User requested stop. Saving...");
        if (workerWindow) workerWindow.close();
    };

    document.getElementById('th-pause').onclick = () => {
        isPaused = !isPaused;
        updateUIState();
    };

    function updateUIState() {
        const stateLabel = document.getElementById('th-state');
        const pauseBtn = document.getElementById('th-pause');
        if (isPaused) {
            stateLabel.innerText = "PAUSED";
            stateLabel.style.color = "#fb0";
            pauseBtn.innerText = "RESUME";
            pauseBtn.style.background = "#0f0";
            updateStatus("⏸️ Paused. Solve Captcha or check Worker!");
        } else {
            stateLabel.innerText = "RUNNING";
            stateLabel.style.color = "#0f0";
            pauseBtn.innerText = "PAUSE";
            pauseBtn.style.background = "#fb0";
        }
    }

    function updateStatus(msg) {
        if (!document.getElementById('th-status')) return;
        document.getElementById('th-status').innerText = msg;
        document.getElementById('th-pages').innerText = stats.countries;
        document.getElementById('th-found').innerText = stats.found;
        document.getElementById('th-scanned').innerText = stats.scanned;
        document.getElementById('th-dupes').innerText = stats.duplicates;
    }

    // --- HELPERS ---
    const delay = ms => new Promise(res => setTimeout(res, ms));

    async function waitWhilePaused() {
        while (isPaused && isRunning) {
            await delay(500);
        }
    }

    function getSeenTokens() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY_SEEN);
            return new Set(raw ? JSON.parse(raw) : []);
        } catch (e) { return new Set(); }
    }

    function saveSeenToken(token) {
        const seen = getSeenTokens();
        seen.add(token);
        localStorage.setItem(STORAGE_KEY_SEEN, JSON.stringify(Array.from(seen)));
    }

    // --- WORKER WINDOW HELPERS ---

    // Check if element is visible IN THE WORKER WINDOW
    function isWorkerElementVisible(elem) {
        if (!elem) return false;
        return elem.offsetParent !== null;
    }

    // Auto-Detect Captcha
    function checkForCaptcha() {
        if (!workerWindow || workerWindow.closed) return false;
        try {
            const text = workerWindow.document.body.innerText;
            if (text.includes("Human-machine verification") || text.includes("Slide to complete puzzle")) {
                if (!isPaused) {
                    console.warn("⚠️ Captcha Detected! Pausing...");
                    isPaused = true;
                    updateUIState();
                    alert("⚠️ CAPTCHA DETECTED in Worker Window!\n\n1. Solve the Captcha in the popup.\n2. Click RESUME on the Controller.");
                }
                return true;
            }
        } catch (e) { }
        return false;
    }

    // Wait for iframe inside Worker Window
    async function waitForWorkerIframe(selector, timeout) {
        let elapsedTime = 0;
        while (elapsedTime < timeout) {
            if (!isRunning) return null;
            await waitWhilePaused(); // Handle pause inside wait

            try {
                // Access Worker Document
                if (workerWindow.closed) return null;
                const doc = workerWindow.document;

                const iframes = doc.querySelectorAll(selector);
                const targetIframe = Array.from(iframes).reverse().find(el => isWorkerElementVisible(el));

                if (targetIframe) {
                    try {
                        const internalDoc = targetIframe.contentDocument || targetIframe.contentWindow.document;
                        if (internalDoc && internalDoc.body) {
                            const text = internalDoc.body.innerText;
                            if (text && text.trim().length > 50) return text;
                        }
                    } catch (e) { }
                }
            } catch (e) { }

            await delay(CHECK_INTERVAL_MS);
            elapsedTime += CHECK_INTERVAL_MS;
        }
        return null;
    }

    function extractData(rawText) {
        const strictTokenRegex = /\b\d{8,10}:[A-Za-z0-9_-]{35}\b/;
        const chatIDRegex = /(?:chatId|chat_id|cid|id)\s*[:=]\s*['"]?(\d+)['"]?/i;
        const tokenMatch = rawText.match(strictTokenRegex);
        const idMatch = rawText.match(chatIDRegex);

        let chatId = idMatch ? idMatch[1] : '';
        if (chatId && chatId.length <= 2) chatId = '';

        return { token: tokenMatch ? tokenMatch[0] : '', chatId: chatId };
    }

    // --- MAIN LOGIC (DRIVING THE WORKER) ---

    async function processWorkerPage() {
        if (!workerWindow || workerWindow.closed) return;

        const doc = workerWindow.document;
        const allElements = doc.querySelectorAll(CLICK_SELECTOR);
        const visibleElements = Array.from(allElements).filter(el => isWorkerElementVisible(el));

        if (visibleElements.length === 0) {
            updateStatus("⚠️ No visible items (or Captcha?)");
            checkForCaptcha();
            return;
        }

        for (let i = 0; i < visibleElements.length; i++) {
            if (!isRunning || workerWindow.closed) break;

            await waitWhilePaused(); // Check pause before each item

            const el = visibleElements[i];

            // Visual feedback in worker
            el.style.border = "3px solid #f0f";
            try {
                el.scrollIntoView({ behavior: 'auto', block: 'center' });
            } catch (e) { }

            updateStatus(`Scanning ${i + 1}/${visibleElements.length}...`);
            stats.scanned++;

            // 1. Click
            el.click();

            // 2. Wait for Popup
            const rawContent = await waitForWorkerIframe(POPUP_IFRAME_SELECTOR, MAX_WAIT_MS);

            // 3. Extract
            if (rawContent) {
                const data = extractData(rawContent);
                if (data.token) {
                    const seen = getSeenTokens();
                    if (seen.has(data.token)) {
                        console.log(`♻️ Dup: ${data.token.substring(0, 10)}`);
                        stats.duplicates++;
                    } else {
                        console.log(`✅ NEW: ${data.token.substring(0, 10)} | ${data.chatId}`);
                        saveSeenToken(data.token);
                        results.push(data);
                        stats.found++;
                    }
                }
            }

            // 4. Close Popup
            // Simulate Escape in Worker
            const escapeEvent = new workerWindow.KeyboardEvent('keydown', {
                key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true
            });
            doc.body.dispatchEvent(escapeEvent);

            el.style.border = "";
            await delay(CLOSE_WAIT_MS);
        }
    }

    // --- INITIALIZATION ---

    try {
        const seenBeforeStart = getSeenTokens();
        console.log(`📜 Loaded ${seenBeforeStart.size} previously seen tokens.`);

        // Open Worker
        updateStatus("🚀 Opening Worker Window...");
        workerWindow = window.open('about:blank', 'th_worker', 'width=1200,height=900');

        if (!workerWindow) {
            alert("❌ POPUP BLOCKED! Please allow popups for fofa.info and try again.");
            isRunning = false;
        } else {
            await delay(1000); // Wait for open
        }

        // Iterate Countries
        for (const country of COUNTRY_CODES) {
            if (!isRunning || workerWindow.closed) break;

            const fullQuery = `${BASE_QUERY} && country="${country}"`;
            const encoded = encodeQuery(fullQuery);
            // FIX: Use current origin to prevent CORS errors (en.fofa.info vs fofa.info)
            const baseUrl = window.location.origin;
            const targetUrl = `${baseUrl}/result?qbase64=${encoded}`;

            updateStatus(` Navigating Worker to: ${country}`);
            stats.countries++;

            // Navigate Worker
            workerWindow.location.href = targetUrl;

            // Wait for load
            updateStatus(`⏳ Loading ${country}...`);
            await delay(PAGE_LOAD_WAIT_MS);

            // CHECK CAPTCHA IMMEDIATELY AFTER LOAD
            checkForCaptcha();
            await waitWhilePaused();

            // Check if loaded (basic check)
            try {
                if (workerWindow.document.readyState !== 'complete') {
                    await delay(2000);
                }
            } catch (e) { }

            // Scan
            updateStatus(`🔎 Scanning results for ${country}...`);
            await processWorkerPage();

            await waitWhilePaused(); // Check one last time before navigating away
        }

    } catch (e) {
        console.error(e);
        alert("Script Error: " + e.message);
    } finally {
        if (workerWindow && !workerWindow.closed) workerWindow.close();
        downloadCSV();
    }

    function downloadCSV() {
        if (results.length > 0) {
            updateStatus("💾 Downloading CSV...");
            let csvContent = "data:text/csv;charset=utf-8,token,chat_id\n";
            results.forEach(row => {
                csvContent += `${row.token.trim()},${row.chatId.trim()}\n`;
            });
            const encodedUri = encodeURI(csvContent);
            const link = document.createElement("a");
            const date = new Date().toISOString().slice(0, 19).replace(/:/g, "-");
            link.setAttribute("href", encodedUri);
            link.setAttribute("download", `telegram_credentials_REMOTE_${date}.csv`);
            document.body.appendChild(link);
            link.click();
            link.remove();
        } else {
            alert("No new credentials found.");
        }
        setTimeout(() => ui.remove(), 5000);
    }

})();
