// --- CONSTANTS ---
const CLICK_SELECTOR = '.el-tooltip.iconfont.icon-daima';
const POPUP_IFRAME_SELECTOR = '.el-dialog__body iframe';

// --- STATE ---
let isWorking = false;

// --- LISTENERS ---
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "SCRAPE_PAGE") {
        if (isWorking) return;
        startScraping();
    } else if (msg.action === "RESUME_WORK") {
        // If we were paused, resume. 
        // For simplicity, just restart scraping checking.
        startScraping();
    }
});

// --- CORE ---
async function startScraping() {
    isWorking = true;
    log("Starting scraping on this page...");

    // 1. Check for Captcha
    if (checkForCaptcha()) {
        chrome.runtime.sendMessage({ action: "CAPTCHA_DETECTED" });
        isWorking = false;
        return;
    }

    // 2. Find Items
    const items = getVisibleItems();
    if (items.length === 0) {
        log("No items found. Moving on.");
        chrome.runtime.sendMessage({ action: "PAGE_COMPLETE" });
        isWorking = false;
        return;
    }

    // 3. Process Items
    for (let i = 0; i < items.length; i++) {
        const el = items[i];

        // Highlight
        el.style.border = "3px solid #f0f";
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });

        // Click
        el.click();

        // Wait for Iframe
        const content = await waitForIframe(POPUP_IFRAME_SELECTOR, 8000);

        if (content) {
            const data = extractData(content);
            if (data.token) {
                chrome.runtime.sendMessage({ action: "RESULT_FOUND", data: data });
                el.style.background = "#0f0"; // Green for success
            }
        }

        // Close / Cleanup
        closePopup();
        el.style.border = "";
        await delay(500);
    }

    // Done
    log("Page complete.");
    chrome.runtime.sendMessage({ action: "PAGE_COMPLETE" });
    isWorking = false;
}

// --- HELPERS ---

function log(msg) {
    console.log("[TH Content]", msg);
    chrome.runtime.sendMessage({ action: "LOG", message: msg });
}

function delay(ms) {
    return new Promise(r => setTimeout(r, ms));
}

function checkForCaptcha() {
    const text = document.body.innerText;
    return text.includes("Human-machine verification") || text.includes("Slide to complete puzzle");
}

function getVisibleItems() {
    const all = document.querySelectorAll(CLICK_SELECTOR);
    return Array.from(all).filter(el => {
        return el.offsetParent !== null; // Visible check
    });
}

async function waitForIframe(selector, timeout) {
    let elapsed = 0;
    while (elapsed < timeout) {
        const iframes = document.querySelectorAll(selector);
        // Find newest visible iframe
        const target = Array.from(iframes).reverse().find(el => el.offsetParent !== null);

        if (target) {
            try {
                const doc = target.contentDocument || target.contentWindow.document;
                if (doc && doc.body && doc.body.innerText.length > 20) {
                    return doc.body.innerText;
                }
            } catch (e) { }
        }
        await delay(500);
        elapsed += 500;
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

function closePopup() {
    // Simulate Escape
    const evt = new KeyboardEvent('keydown', {
        key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true
    });
    document.body.dispatchEvent(evt);

    // Also try clicking close buttons if any exist
    // .el-dialog__headerbtn
    const closeBtn = document.querySelector('.el-dialog__headerbtn');
    if (closeBtn) closeBtn.click();
}
