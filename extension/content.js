// --- CONSTANTS ---
const CLICK_SELECTOR = 'i.iconfont.icon-daima';  // Code view icon button
const POPUP_CONTENT_SELECTOR = '.source-content';  // The div containing HTML source
const POPUP_DIALOG_SELECTOR = '.el-dialog__body';  // Fallback: the dialog body
const RESULT_ROW_SELECTOR = '.hsxa-host-table-body .hsxa-host-table-body-item, .host-table-body .host-table-body-item, [class*="result-item"], [class*="host-item"]';

// Token regex reused in multiple places
const TOKEN_REGEX = /(\d{8,10}:AA[A-Za-z0-9_-]{33})/g;

// --- STATE ---
let isWorking = false;
let shouldStop = false;

// --- LISTENERS ---
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "SCRAPE_PAGE") {
        shouldStop = false;
        if (isWorking) return;
        startScraping();
    } else if (msg.action === "RESUME_WORK") {
        shouldStop = false;
        startScraping();
    } else if (msg.action === "STOP_WORK") {
        shouldStop = true;
        isWorking = false;
        log("Stopped by user.");
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

    // 2. Fast pass: scan the entire page text for tokens without clicking anything.
    //    FOFA sometimes shows tokens in URLs, titles, or snippets directly.
    const fastTokens = extractTokensFromText(document.body.innerText);
    if (fastTokens.length > 0) {
        log(`Fast pass: found ${fastTokens.length} token(s) in page text`);
        for (const token of fastTokens) {
            if (shouldStop) break;
            chrome.runtime.sendMessage({ action: "RESULT_FOUND", data: { token, chatId: "" } });
        }
    }

    // 3. Click pass: open each result's source popup for deeper extraction.
    //    Skip this if shouldStop is already set.
    const items = getVisibleItems();
    if (items.length === 0 || shouldStop) {
        log(shouldStop ? "Aborted." : "No clickable items. Moving on.");
        if (!shouldStop) chrome.runtime.sendMessage({ action: "PAGE_COMPLETE" });
        isWorking = false;
        return;
    }

    log(`Click pass: processing ${items.length} item(s)...`);

    for (let i = 0; i < items.length; i++) {
        if (shouldStop) {
            log("Aborted mid-page.");
            isWorking = false;
            return;
        }

        const el = items[i];

        el.style.border = "3px solid #f0f";
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.click();

        // Wait for popup — but bail out early if FOFA shows a timeout/error notice
        const content = await waitForPopupContent(5000);

        if (content) {
            log(`Popup content (${content.length} chars)`);
            const data = extractData(content);
            log(`Extracted: token=${data.token ? 'YES' : 'NO'}, chatId=${data.chatId || 'NO'}`);
            if (data.token) {
                chrome.runtime.sendMessage({ action: "RESULT_FOUND", data: data });
                el.style.background = "#0f0";
            }
        } else {
            log("Popup timed out or empty — skipping");
        }

        closePopup();
        el.style.border = "";

        if (shouldStop) break;
        await delay(300);
    }

    log("Page complete.");
    if (!shouldStop) {
        chrome.runtime.sendMessage({ action: "PAGE_COMPLETE" });
    }
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

async function waitForPopupContent(timeout) {
    let elapsed = 0;
    while (elapsed < timeout) {
        // Try .source-content first (where HTML source is displayed)
        let sourceDiv = document.querySelector(POPUP_CONTENT_SELECTOR);
        if (sourceDiv && sourceDiv.innerText.length > 50) {
            // Decode HTML entities (FOFA shows &lt; &gt; etc)
            return decodeHTMLEntities(sourceDiv.innerText);
        }

        // Fallback: try the dialog body directly
        let dialogBody = document.querySelector(POPUP_DIALOG_SELECTOR);
        if (dialogBody && dialogBody.innerText.length > 50) {
            return decodeHTMLEntities(dialogBody.innerText);
        }

        // Also try iframes (some pages use them)
        const iframes = document.querySelectorAll('.el-dialog__body iframe');
        for (const iframe of iframes) {
            try {
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                if (doc && doc.body && doc.body.innerText.length > 50) {
                    return doc.body.innerText;
                }
            } catch (e) { /* cross-origin, ignore */ }
        }

        await delay(500);
        elapsed += 500;
    }
    return null;
}

// Decode HTML entities like &lt; &gt; &amp;
function decodeHTMLEntities(text) {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
}

function extractData(rawText) {
    // Token regex: matches Telegram bot tokens in various formats
    // Handles: BOT_TOKEN: '...', token = "...", "token": "...", or raw token
    // Format: 8-10 digit bot_id : 35 char secret starting with "AA"
    // Example: 8514017233:AAEAPjYrm0bIUvYgvzP68IlmAU14CBOt94E
    const tokenRegex = /["']?(\d{8,10}:AA[A-Za-z0-9_-]{33})["']?/g;

    // ChatId regex: handles various JavaScript patterns
    // Matches: CHAT_ID: '6394582655', chat_id = "-100...", chatId: 123
    const chatIdRegex = /(?:CHAT_ID|chat_id|chatId|chat|target|cid)\s*[=:]\s*["']?(-?\d{5,20})["']?/gi;

    // Find all tokens (there might be multiple)
    const tokens = [];
    let tokenMatch;
    while ((tokenMatch = tokenRegex.exec(rawText)) !== null) {
        tokens.push(tokenMatch[1]);
    }

    // Find chatId - try multiple patterns
    let chatId = '';
    const idMatches = rawText.match(chatIdRegex);
    if (idMatches && idMatches.length > 0) {
        // Extract just the number from the first matched pattern
        const numMatch = idMatches[0].match(/-?\d{5,20}/);
        if (numMatch) chatId = numMatch[0];
    }

    // Debug logging
    console.log("[TH Extract] Found tokens:", tokens);
    console.log("[TH Extract] Found chatId:", chatId);
    console.log("[TH Extract] Raw text sample:", rawText.substring(0, 500));

    // Return first token found (most common case)
    return {
        token: tokens.length > 0 ? tokens[0] : '',
        chatId: chatId,
        allTokens: tokens  // Include all tokens in case multiple found
    };
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
