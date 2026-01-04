(async function () {
    // --- CONFIGURATION ---
    const CLICK_SELECTOR = '.el-tooltip.iconfont.icon-daima';
    const POPUP_IFRAME_SELECTOR = '.el-dialog__body iframe';

    // TIMERS
    const MAX_WAIT_MS = 10000;
    const CHECK_INTERVAL_MS = 500;
    const CLOSE_WAIT_MS = 800;

    // STATE
    let isRunning = true;
    let stats = { scanned: 0, found: 0, duplicates: 0 };
    let results = [];

    // --- UI OVERLAY ---
    const ui = document.createElement('div');
    ui.id = 'th-simple-ui';
    ui.style.cssText = `
        position: fixed; bottom: 20px; right: 20px; width: 300px;
        background: rgba(0, 0, 0, 0.9); color: #0f0; border: 1px solid #0f0;
        padding: 15px; font-family: monospace; z-index: 99999;
        box-shadow: 0 0 10px rgba(0, 255, 0, 0.2); border-radius: 5px;
    `;
    ui.innerHTML = `
        <h3 style="margin: 0 0 10px; color: #fff; border-bottom: 1px solid #333; padding-bottom: 5px;">
            🕵️ Simple Scraper
        </h3>
        <div id="th-status" style="margin-bottom: 10px; font-size: 12px;">Running...</div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px; font-size: 11px; margin-bottom: 10px;">
            <div>Found: <span id="th-found">0</span></div>
            <div>Scanned: <span id="th-scanned">0</span></div>
        </div>
        <button id="th-stop" style="
            width: 100%; background: #c00; color: #fff; border: none; 
            padding: 5px; cursor: pointer; font-weight: bold;">
            STOP & SAVE
        </button>
    `;

    if (document.getElementById('th-simple-ui')) document.getElementById('th-simple-ui').remove();
    document.body.appendChild(ui);

    document.getElementById('th-stop').onclick = () => {
        isRunning = false;
        updateStatus("🛑 Stopping...");
    };

    function updateStatus(msg) {
        if (!document.getElementById('th-status')) return;
        document.getElementById('th-status').innerText = msg;
        document.getElementById('th-found').innerText = stats.found;
        document.getElementById('th-scanned').innerText = stats.scanned;
    }

    // --- HELPERS ---
    const delay = ms => new Promise(res => setTimeout(res, ms));

    function pressEscape() {
        const escapeEvent = new KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true
        });
        document.body.dispatchEvent(escapeEvent);
    }

    function isVisible(elem) {
        if (!elem) return false;
        return elem.offsetParent !== null;
    }

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
                } catch (e) { }
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
        if (chatId && chatId.length <= 2) chatId = '';

        return {
            token: tokenMatch ? tokenMatch[0] : '',
            chatId: chatId
        };
    }

    // --- MAIN ---
    try {
        const allElements = document.querySelectorAll(CLICK_SELECTOR);
        const visibleElements = Array.from(allElements).filter(el => isVisible(el));

        if (visibleElements.length === 0) {
            alert("No visible items found to scrape on this page.");
        } else {
            console.log(`Found ${visibleElements.length} items to scan.`);

            for (let i = 0; i < visibleElements.length; i++) {
                if (!isRunning) break;
                const el = visibleElements[i];

                el.style.border = "2px solid #0f0";
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                updateStatus(`Scanning ${i + 1}/${visibleElements.length}...`);
                stats.scanned++;

                el.click();
                const rawContent = await waitForIframeContent(POPUP_IFRAME_SELECTOR, MAX_WAIT_MS);

                if (rawContent) {
                    const data = extractData(rawContent);
                    if (data.token) {
                        // Simple dup check against current run results
                        if (results.some(r => r.token === data.token)) {
                            console.log("Dup skipped.");
                        } else {
                            console.log(`✅ ${data.token}`);
                            results.push(data);
                            stats.found++;
                        }
                    }
                }

                el.style.border = "";
                pressEscape();
                await delay(CLOSE_WAIT_MS);
            }
        }

    } catch (e) {
        console.error(e);
        alert("Error: " + e.message);
    } finally {
        downloadCSV();
    }

    function downloadCSV() {
        if (results.length > 0) {
            updateStatus("💾 Downloading...");
            let csvContent = "data:text/csv;charset=utf-8,token,chat_id\n";
            results.forEach(row => {
                csvContent += `${row.token.trim()},${row.chatId.trim()}\n`;
            });
            const encodedUri = encodeURI(csvContent);
            const link = document.createElement("a");
            const date = new Date().toISOString().slice(0, 19).replace(/:/g, "-");
            link.setAttribute("href", encodedUri);
            link.setAttribute("download", `telegram_simple_${date}.csv`);
            document.body.appendChild(link);
            link.click();
            link.remove();
        } else {
            alert("No credentials found.");
        }
        setTimeout(() => ui.remove(), 2000);
    }
})();
