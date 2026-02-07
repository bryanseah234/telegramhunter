document.addEventListener('DOMContentLoaded', () => {
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');
    const btnResume = document.getElementById('btn-resume');
    const btnDownload = document.getElementById('btn-download');
    const inputQuery = document.getElementById('input-query');
    const selectDomain = document.getElementById('select-domain');

    // Load State
    chrome.runtime.sendMessage({ action: "GET_STATE" }, (response) => {
        if (chrome.runtime.lastError) {
            console.log("Background not ready:", chrome.runtime.lastError.message);
            return;
        }
        updateUI(response);
    });

    // Listen to updates
    chrome.runtime.onMessage.addListener((msg) => {
        if (msg.action === "STATE_UPDATE") updateUI(msg.state);
    });

    btnStart.onclick = () => {
        const query = inputQuery.value;
        const domain = selectDomain.value;
        chrome.runtime.sendMessage({ action: "START_SCAN", query: query, domain: domain });
    };

    btnStop.onclick = () => {
        chrome.runtime.sendMessage({ action: "STOP_SCAN" });
    };

    btnResume.onclick = () => {
        chrome.runtime.sendMessage({ action: "RESUME_SCAN" });
    };

    btnDownload.onclick = () => {
        chrome.runtime.sendMessage({ action: "DOWNLOAD_RESULTS" }); // handled in bg or logic
    };

    function updateUI(state) {
        if (!state) return;

        document.getElementById('status').innerText = state.status;
        document.getElementById('count-country').innerText = state.countriesDone;
        document.getElementById('count-found').innerText = state.resultsFound;
        if (document.getElementById('count-valid')) {
            document.getElementById('count-valid').innerText = state.resultsValid || 0;
        }

        // Restore domain selection if state exists and not running (optional but nice)
        if (state.domain && !state.isRunning) {
            selectDomain.value = state.domain;
        }

        // Buttons
        // Buttons Logic
        if (state.isRunning) {
            btnStart.classList.add('hidden');
            btnStop.classList.remove('hidden');
            inputQuery.disabled = true;
            selectDomain.disabled = true;
            btnStart.innerText = "🚀 Start"; // Reset text
        } else {
            // STOPPED or READY
            btnStop.classList.add('hidden');
            inputQuery.disabled = false;
            selectDomain.disabled = false;

            if (state.resultsFound > 0) {
                // FINISHED / STOPPED with Data
                btnStart.classList.remove('hidden');
                btnStart.innerText = "🔄 New Scan (Clears Data)";
                btnStart.style.backgroundColor = "#ff9800"; // Warning color

                // Highlight Download
                btnDownload.style.border = "2px solid #4CAF50";
            } else {
                // READY (No Data)
                btnStart.classList.remove('hidden');
                btnStart.innerText = "🚀 Start";
                btnStart.style.backgroundColor = ""; // Default
                btnDownload.style.border = "";
            }
        }

        if (state.isPaused) {
            btnStop.classList.add('hidden');
            btnResume.classList.remove('hidden');
            document.getElementById('status').innerText = "PAUSED (Captcha?)";
            document.getElementById('status').style.color = "red";
        } else {
            btnResume.classList.add('hidden');
            document.getElementById('status').style.color = "#fb0";
        }

        btnDownload.disabled = (state.resultsFound === 0);
    }
});
