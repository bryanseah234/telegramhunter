(async function () {
    // --- CONFIGURATION ---
    const CLICK_SELECTOR = '.el-tooltip.iconfont.icon-daima';
    const POPUP_IFRAME_SELECTOR = '.el-dialog__body iframe';
    const NEXT_PAGE_SELECTOR = '.el-pagination .btn-next'; // Fofa next page button

    // STORAGE KEYS
    const STORAGE_KEY_SEEN = 'tg_hunter_seen_tokens'; // Set of seen token hashes or tokens

    // TIMERS
    const MAX_WAIT_MS = 15000;
    const CHECK_INTERVAL_MS = 500;
    const CLOSE_WAIT_MS = 1000;
    const PAGE_LOAD_WAIT_MS = 5000; // Wait for new page to load

    // STATE
    let isRunning = true;
    let stats = { pages: 0, scanned: 0, found: 0, duplicates: 0 };
    let results = [];

    // --- UI OVERLAY ---
    const ui = document.createElement('div');
    ui.style.cssText = `
        position: fixed; bottom: 20px; right: 20px; width: 300px;
        background: rgba(0, 0, 0, 0.9); color: #0f0; border: 1px solid #0f0;
        padding: 15px; font-family: monospace; z-index: 99999;
        box-shadow: 0 0 10px rgba(0, 255, 0, 0.2); border-radius: 5px;
    `;
    ui.innerHTML = `
        <h3 style="margin: 0 0 10px; color: #fff; border-bottom: 1px solid #333; padding-bottom: 5px;">
            🕵️ Telegram Hunter
        </h3>
        <div id="th-status" style="margin-bottom: 10px; font-size: 12px;">Initializing...</div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px; font-size: 11px; margin-bottom: 10px;">
            <div>Pages: <span id="th-pages">0</span></div>
            <div>Found: <span id="th-found">0</span></div>
            <div>Scanned: <span id="th-scanned">0</span></div>
            <div>Dupes: <span id="th-dupes">0</span></div>
        </div>
        <button id="th-stop" style="
            width: 100%; background: #c00; color: #fff; border: none; 
            padding: 5px; cursor: pointer; font-weight: bold;">
            STOP & DOWNLOAD CSV
        </button>
    `;
    document.body.appendChild(ui);

    document.getElementById('th-stop').onclick = () => {
        isRunning = false;
        updateStatus("🛑 Stopping by user request...");
    };

    function updateStatus(msg) {
        document.getElementById('th-status').innerText = msg;
        document.getElementById('th-pages').innerText = stats.pages;
        document.getElementById('th-found').innerText = stats.found;
        document.getElementById('th-scanned').innerText = stats.scanned;
        document.getElementById('th-dupes').innerText = stats.duplicates;
    }

    // --- HELPERS ---
    const delay = ms => new Promise(res => setTimeout(res, ms));

    function pressEscape() {
        const escapeEvent = new KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', keyCode: 27, which: 27,
            bubbles: true, cancelable: true
        });
        document.body.dispatchEvent(escapeEvent);
        if (document.activeElement) document.activeElement.dispatchEvent(escapeEvent);
    }

    function isVisible(elem) {
        if (!elem) return false;
        if (elem.offsetParent === null) return false;
        const rect = elem.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    // Load seen tokens from Storage
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

    // --- SMART WAIT FOR IFRAME ---
    async function waitForIframeContent(selector, timeout) {
        let elapsedTime = 0;
        while (elapsedTime < timeout) {
            const iframes = document.querySelectorAll(selector);
            const targetIframe = Array.from(iframes).reverse().find(el => isVisible(el));

            if (targetIframe) {
                try {
                    const internalDoc = targetIframe.contentDocument || targetIframe.contentWindow.document;
                    if (internalDoc && internalDoc.body) {
                        const text = internalDoc.body.innerText;
                        if (text && text.trim().length > 50) return text;
                    }
                } catch (e) {
                    console.warn("⚠️ Security restriction (CORS).");
                    return null;
                }
            }
            if (!isRunning) return null;
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
        // Validate Chat ID: Must be > 2 chars to be considered valid
        if (chatId && chatId.length <= 2) {
            chatId = '';
        }

        return {
            token: tokenMatch ? tokenMatch[0] : '',
            chatId: chatId
        };
    }

    // --- CORE LOGIC ---

    async function processPage() {
        const allElements = document.querySelectorAll(CLICK_SELECTOR);
        const visibleElements = Array.from(allElements).filter(el => isVisible(el));

        updateStatus(`🔍 Processing Page ${stats.pages + 1}... (${visibleElements.length} items)`);

        for (let i = 0; i < visibleElements.length; i++) {
            if (!isRunning) break;

            const el = visibleElements[i];

            // Highlight current element
            el.style.border = "2px solid #0f0";
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });

            updateStatus(`Scanning item ${i + 1}/${visibleElements.length}...`);
            stats.scanned++;

            // 1. Click
            el.click();

            // 2. Wait
            const rawContent = await waitForIframeContent(POPUP_IFRAME_SELECTOR, MAX_WAIT_MS);

            // 3. Scrape
            if (rawContent) {
                const data = extractData(rawContent);
                if (data.token) {
                    // Deduplication check
                    const seen = getSeenTokens();
                    if (seen.has(data.token)) {
                        console.log(`♻️ Duplicate skipped: ${data.token.substring(0, 10)}...`);
                        stats.duplicates++;
                    } else {
                        console.log(`✅ Token: ${data.token.substring(0, 10)}... | ChatID: ${data.chatId}`);
                        saveSeenToken(data.token);
                        results.push(data);
                        stats.found++;
                    }
                }
            }

            // 4. Reset/Exit
            el.style.border = "";
            pressEscape();
            await delay(CLOSE_WAIT_MS);
        }
    }

    // --- MAIN LOOP ---
    try {
        const seenBeforeStart = getSeenTokens();
        console.log(`📜 Loaded ${seenBeforeStart.size} previously seen tokens.`);

        while (isRunning) {
            stats.pages++;
            await processPage();

            if (!isRunning) break;

            // Try Pagination
            const nextBtn = document.querySelector(NEXT_PAGE_SELECTOR);
            if (nextBtn && !nextBtn.disabled && !nextBtn.classList.contains('disabled')) {
                updateStatus("➡️ Moving to Next Page...");
                nextBtn.click();
                await delay(PAGE_LOAD_WAIT_MS);
            } else {
                updateStatus("🏁 No more pages or Next button disabled.");
                break;
            }
        }
    } catch (e) {
        console.error("Critical Script Error:", e);
        alert("Script Error: " + e.message);
    } finally {
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
            link.setAttribute("download", `telegram_credentials_${date}.csv`);
            document.body.appendChild(link);
            link.click();
            link.remove();
        } else {
            alert("No new credentials found in this run.");
        }

        // Cleanup UI
        setTimeout(() => ui.remove(), 5000);
    }
})();
