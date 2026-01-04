document.addEventListener('DOMContentLoaded', () => {
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');
    const btnResume = document.getElementById('btn-resume');
    const btnDownload = document.getElementById('btn-download');
    const inputQuery = document.getElementById('input-query');
    const selectDomain = document.getElementById('select-domain');

    // Load State
    chrome.runtime.sendMessage({ action: "GET_STATE" }, (response) => {
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
        if (state.isRunning) {
            btnStart.classList.add('hidden');
            btnStop.classList.remove('hidden');
            inputQuery.disabled = true;
            selectDomain.disabled = true;
        } else {
            btnStart.classList.remove('hidden');
            btnStop.classList.add('hidden');
            inputQuery.disabled = false;
            selectDomain.disabled = false;
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
