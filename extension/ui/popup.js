document.addEventListener("DOMContentLoaded", () => {
    const btnStart      = document.getElementById("btn-start");
    const btnStop       = document.getElementById("btn-stop");
    const btnResume     = document.getElementById("btn-resume");
    const btnUpload     = document.getElementById("btn-upload");
    const inputQuery    = document.getElementById("input-query");
    const selectDomain  = document.getElementById("select-domain");
    const inputApiUrl   = document.getElementById("input-api-url");
    const inputMonKey   = document.getElementById("input-monitor-key");
    const elTotal       = document.getElementById("count-total");

    // Load saved config
    chrome.storage.sync.get(["supabase_config"], (result) => {
        const cfg = result.supabase_config || {};
        if (cfg.apiUrl)     inputApiUrl.value = cfg.apiUrl;
        if (cfg.monitorKey) inputMonKey.value = cfg.monitorKey;
    });

    function saveConfig() {
        chrome.storage.sync.set({
            supabase_config: {
                apiUrl:     (inputApiUrl.value || "").trim(),
                monitorKey: (inputMonKey.value || "").trim(),
            }
        });
    }

    inputApiUrl.onchange = saveConfig;
    inputMonKey.onchange = saveConfig;

    // Get initial state
    chrome.runtime.sendMessage({ action: "GET_STATE" }, (response) => {
        if (chrome.runtime.lastError) return;
        updateUI(response);
    });

    // Live updates while popup is open
    chrome.runtime.onMessage.addListener((msg) => {
        if (msg.action === "STATE_UPDATE") updateUI(msg.state);
    });

    btnStart.onclick = () => {
        saveConfig();
        chrome.runtime.sendMessage({
            action: "START_SCAN",
            query:  inputQuery.value,
            domain: selectDomain.value,
        });
    };

    btnStop.onclick   = () => chrome.runtime.sendMessage({ action: "STOP_SCAN" });
    btnResume.onclick = () => chrome.runtime.sendMessage({ action: "RESUME_SCAN" });
    btnUpload.onclick = () => {
        saveConfig();
        chrome.runtime.sendMessage({ action: "UPLOAD_RESULTS" });
    };

    function updateUI(state) {
        if (!state) return;

        document.getElementById("status").innerText        = state.status;
        document.getElementById("count-country").innerText = state.countriesDone || 0;
        document.getElementById("count-found").innerText   = state.resultsFound  || 0;
        const validEl = document.getElementById("count-valid");
        if (validEl) validEl.innerText = state.resultsValid || 0;
        // Show total countries if available
        if (elTotal && state.countryList) elTotal.innerText = state.countryList.length;

        if (!state.isRunning && state.domain) selectDomain.value = state.domain;

        if (state.isRunning && !state.isPaused) {
            btnStart.classList.add("hidden");
            btnStop.classList.remove("hidden");
            btnResume.classList.add("hidden");
            inputQuery.disabled   = true;
            selectDomain.disabled = true;
        } else if (state.isPaused) {
            btnStop.classList.add("hidden");
            btnResume.classList.remove("hidden");
            btnStart.classList.add("hidden");
            document.getElementById("status").innerText = "⚠️ PAUSED — solve captcha then Resume";
            document.getElementById("status").style.color = "red";
        } else {
            btnStop.classList.add("hidden");
            btnResume.classList.add("hidden");
            btnStart.classList.remove("hidden");
            inputQuery.disabled   = false;
            selectDomain.disabled = false;
            document.getElementById("status").style.color = "#fb0";

            if (state.resultsFound > 0) {
                btnStart.innerText             = "🔄 New Scan (clears data)";
                btnStart.style.backgroundColor = "#ff9800";
            } else {
                btnStart.innerText             = "🚀 Start";
                btnStart.style.backgroundColor = "";
            }
        }

        // Upload button: only active when there are valid results and not running
        btnUpload.disabled = !(state.resultsValid > 0 && !state.isRunning);
    }
});
