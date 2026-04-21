document.addEventListener("DOMContentLoaded", () => {
    const btnStart       = document.getElementById("btn-start");
    const btnStop        = document.getElementById("btn-stop");
    const btnResume      = document.getElementById("btn-resume");
    const btnUpload      = document.getElementById("btn-upload");
    const inputQuery     = document.getElementById("input-query");
    const selectDomain   = document.getElementById("select-domain");
    const inputSbUrl        = document.getElementById("input-supabase-url");
    const inputSbKey        = document.getElementById("input-supabase-key");
    const inputExtSecret    = document.getElementById("input-extension-secret");
    const inputApiUrl       = document.getElementById("input-api-url");

    // Config is stored in chrome.storage.sync so it follows your Chrome login
    // across machines — paste once, works everywhere you're signed into Chrome.
    chrome.storage.sync.get(["supabase_config"], (result) => {
        const cfg = result.supabase_config || {};
        if (cfg.supabaseUrl)      inputSbUrl.value     = cfg.supabaseUrl;
        if (cfg.supabaseKey)      inputSbKey.value     = cfg.supabaseKey;
        if (cfg.extensionSecret)  inputExtSecret.value = cfg.extensionSecret;
        if (cfg.apiUrl)           inputApiUrl.value    = cfg.apiUrl;
    });

    function saveSupabaseConfig() {
        chrome.storage.sync.set({
            supabase_config: {
                supabaseUrl:     (inputSbUrl.value     || "").trim(),
                supabaseKey:     (inputSbKey.value     || "").trim(),
                extensionSecret: (inputExtSecret.value || "").trim(),
                apiUrl:          (inputApiUrl.value    || "").trim(),
            }
        });
    }

    inputSbUrl.onchange      = saveSupabaseConfig;
    inputSbKey.onchange      = saveSupabaseConfig;
    inputExtSecret.onchange  = saveSupabaseConfig;
    inputApiUrl.onchange     = saveSupabaseConfig;

    // Get initial state
    chrome.runtime.sendMessage({ action: "GET_STATE" }, (response) => {
        if (chrome.runtime.lastError) return;
        updateUI(response);
    });

    // Live updates
    chrome.runtime.onMessage.addListener((msg) => {
        if (msg.action === "STATE_UPDATE") updateUI(msg.state);
    });

    btnStart.onclick = () => {
        chrome.runtime.sendMessage({
            action: "START_SCAN",
            query: inputQuery.value,
            domain: selectDomain.value
        });
    };

    btnStop.onclick   = () => chrome.runtime.sendMessage({ action: "STOP_SCAN" });
    btnResume.onclick = () => chrome.runtime.sendMessage({ action: "RESUME_SCAN" });

    btnUpload.onclick = () => {
        saveSupabaseConfig();
        chrome.runtime.sendMessage({ action: "UPLOAD_RESULTS" });
    };

    function updateUI(state) {
        if (!state) return;

        document.getElementById("status").innerText        = state.status;
        document.getElementById("count-country").innerText = state.countriesDone;
        document.getElementById("count-found").innerText   = state.resultsFound;
        const validEl = document.getElementById("count-valid");
        if (validEl) validEl.innerText = state.resultsValid || 0;

        if (state.domain && !state.isRunning) selectDomain.value = state.domain;

        if (state.isRunning) {
            btnStart.classList.add("hidden");
            btnStop.classList.remove("hidden");
            inputQuery.disabled    = true;
            selectDomain.disabled  = true;
            btnStart.innerText     = "🚀 Start";
        } else {
            btnStop.classList.add("hidden");
            inputQuery.disabled   = false;
            selectDomain.disabled = false;

            if (state.resultsFound > 0) {
                btnStart.classList.remove("hidden");
                btnStart.innerText            = "🔄 New Scan (Clears Data)";
                btnStart.style.backgroundColor = "#ff9800";
                btnUpload.style.border         = "2px solid #4CAF50";
            } else {
                btnStart.classList.remove("hidden");
                btnStart.innerText            = "🚀 Start";
                btnStart.style.backgroundColor = "";
                btnUpload.style.border         = "";
            }
        }

        if (state.isPaused) {
            btnStop.classList.add("hidden");
            btnResume.classList.remove("hidden");
            document.getElementById("status").innerText    = "PAUSED (Captcha?)";
            document.getElementById("status").style.color  = "red";
        } else {
            btnResume.classList.add("hidden");
            document.getElementById("status").style.color  = "#fb0";
        }

        // Enable upload only when there are valid results
        btnUpload.disabled = !(state.resultsValid > 0);
    }
});
