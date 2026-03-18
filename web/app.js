/**
 * MyAi Web UI — WebSocket chat client with authentication.
 */

(function () {
    "use strict";

    // -- Config --
    const DEFAULT_WS_URL = `ws://${location.host}/ws`;
    const RECONNECT_DELAY_MS = 3000;
    const MAX_RECONNECT_ATTEMPTS = 10;

    // -- State --
    let ws = null;
    let reconnectAttempts = 0;
    let reconnectTimer = null;
    let settings = loadSettings();
    let currentUser = null;
    let authToken = localStorage.getItem("myai_auth_token") || null;

    // -- DOM refs --
    const $messages = document.getElementById("messages");
    const $form = document.getElementById("chat-form");
    const $input = document.getElementById("chat-input");
    const $sendBtn = document.getElementById("btn-send");
    const $typing = document.getElementById("typing-indicator");
    const $connStatus = document.getElementById("connection-status");
    const $sidebar = document.getElementById("sidebar");
    const $toggleSidebar = document.getElementById("btn-toggle-sidebar");
    const $clearBtn = document.getElementById("btn-clear");
    const $settingsModal = document.getElementById("settings-modal");
    const $closeSettings = document.getElementById("btn-close-settings");
    const $saveSettings = document.getElementById("btn-save-settings");
    const $settingUserId = document.getElementById("setting-user-id");
    const $settingUserName = document.getElementById("setting-user-name");
    const $settingWsUrl = document.getElementById("setting-ws-url");

    // Status panel refs
    const $statusOllama = document.getElementById("status-ollama");
    const $statusModel = document.getElementById("status-model");
    const $statusGraph = document.getElementById("status-graph");
    const $statusSearch = document.getElementById("status-search");
    const $searchToggle = document.getElementById("search-toggle");
    const $skillsList = document.getElementById("skills-list");

    // Auth refs
    const $authScreen = document.getElementById("auth-screen");
    const $setupForm = document.getElementById("setup-form");
    const $loginForm = document.getElementById("login-form");
    const $authLoading = document.getElementById("auth-loading");
    const $chatArea = document.getElementById("chat-area");
    const $logoutBtn = document.getElementById("btn-logout");
    const $userDisplayName = document.getElementById("user-display-name");
    const $userRoleBadge = document.getElementById("user-role-badge");

    // -- Init --
    function init() {
        bindAuthEvents();
        checkSetup();
    }

    function loadSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem("myai_settings") || "{}");
            return {
                userId: saved.userId || "web-user-" + Math.random().toString(36).slice(2, 8),
                userName: saved.userName || "User",
                wsUrl: saved.wsUrl || "",
            };
        } catch {
            return { userId: "web-user-1", userName: "User", wsUrl: "" };
        }
    }

    function saveSettings() {
        localStorage.setItem("myai_settings", JSON.stringify(settings));
    }

    // -- Auth Flow --
    async function checkSetup() {
        try {
            const res = await fetch("/api/auth/setup-status");
            const data = await res.json();

            $authLoading.classList.add("hidden");

            if (!data.setup_complete) {
                // Show setup form
                $setupForm.classList.remove("hidden");
                $loginForm.classList.add("hidden");
            } else {
                // Check for existing token
                if (authToken) {
                    const valid = await validateToken();
                    if (valid) {
                        showChat();
                        return;
                    }
                    // Token invalid, clear it
                    authToken = null;
                    localStorage.removeItem("myai_auth_token");
                }
                // Show login form
                $loginForm.classList.remove("hidden");
                $setupForm.classList.add("hidden");
            }
        } catch (err) {
            $authLoading.classList.add("hidden");
            // Server might not be running, show login form as default
            $loginForm.classList.remove("hidden");
        }
    }

    async function validateToken() {
        try {
            const res = await fetch("/api/auth/me", {
                headers: { "Authorization": "Bearer " + authToken },
            });
            if (!res.ok) return false;
            const data = await res.json();
            if (data.user) {
                currentUser = data.user;
                return true;
            }
            return false;
        } catch {
            return false;
        }
    }

    async function setupAdmin(e) {
        e.preventDefault();
        const email = document.getElementById("setup-email").value.trim();
        const displayName = document.getElementById("setup-name").value.trim();
        const password = document.getElementById("setup-password").value;
        const confirmPassword = document.getElementById("setup-confirm-password").value;
        const $error = document.getElementById("setup-error");

        $error.classList.add("hidden");

        if (!email || !displayName || !password) {
            showAuthError($error, "All fields are required.");
            return;
        }

        if (password.length < 6) {
            showAuthError($error, "Password must be at least 6 characters.");
            return;
        }

        if (password !== confirmPassword) {
            showAuthError($error, "Passwords do not match.");
            return;
        }

        try {
            const res = await fetch("/api/auth/setup", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, display_name: displayName, password }),
            });
            const data = await res.json();

            if (!res.ok) {
                showAuthError($error, data.error || "Setup failed.");
                return;
            }

            // Setup successful, store token
            authToken = data.token;
            currentUser = data.user;
            localStorage.setItem("myai_auth_token", authToken);
            showChat();
        } catch (err) {
            showAuthError($error, "Connection failed. Is MyAi running?");
        }
    }

    async function login(e) {
        e.preventDefault();
        const email = document.getElementById("login-email").value.trim();
        const password = document.getElementById("login-password").value;
        const $error = document.getElementById("login-error");

        $error.classList.add("hidden");

        if (!email || !password) {
            showAuthError($error, "Email and password are required.");
            return;
        }

        try {
            const res = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password }),
            });
            const data = await res.json();

            if (!res.ok) {
                showAuthError($error, data.error || "Login failed.");
                return;
            }

            authToken = data.token;
            currentUser = data.user;
            localStorage.setItem("myai_auth_token", authToken);
            showChat();
        } catch (err) {
            showAuthError($error, "Connection failed. Is MyAi running?");
        }
    }

    async function logout() {
        try {
            if (authToken) {
                await fetch("/api/auth/logout", {
                    method: "POST",
                    headers: { "Authorization": "Bearer " + authToken },
                });
            }
        } catch {
            // Ignore errors during logout
        }

        authToken = null;
        currentUser = null;
        localStorage.removeItem("myai_auth_token");

        // Disconnect WebSocket
        disconnect();

        // Show auth screen
        $authScreen.classList.remove("hidden");
        $sidebar.classList.add("hidden");
        $chatArea.classList.add("hidden");
        $messages.innerHTML = "";

        // Reset reconnect
        reconnectAttempts = 0;

        // Re-check setup to show appropriate form
        $authLoading.classList.remove("hidden");
        $loginForm.classList.add("hidden");
        $setupForm.classList.add("hidden");
        checkSetup();
    }

    function showAuthError($el, message) {
        $el.textContent = message;
        $el.classList.remove("hidden");
    }

    function showChat() {
        // Hide auth screen, show chat
        $authScreen.classList.add("hidden");
        $sidebar.classList.remove("hidden");
        $chatArea.classList.remove("hidden");

        // Update user info in sidebar
        if (currentUser) {
            $userDisplayName.textContent = currentUser.display_name || "User";
            $userRoleBadge.textContent = formatRoleName(currentUser.role_level);
            $userRoleBadge.className = "user-role-badge role-" + currentUser.role_level;
        }

        // Populate settings fields
        $settingUserId.value = settings.userId;
        $settingUserName.value = settings.userName;
        $settingWsUrl.value = settings.wsUrl || DEFAULT_WS_URL;

        showWelcome();
        connect();
        bindChatEvents();
        fetchStatus();
        fetchSkills();
    }

    function formatRoleName(role) {
        if (!role) return "User";
        return role.replace(/_/g, " ").replace(/\b\w/g, function (c) {
            return c.toUpperCase();
        });
    }

    // -- WebSocket --
    function connect() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        const url = settings.wsUrl || DEFAULT_WS_URL;
        setConnStatus("connecting", "Connecting...");

        try {
            ws = new WebSocket(url);
        } catch (e) {
            setConnStatus("disconnected", "Failed");
            scheduleReconnect();
            return;
        }

        ws.onopen = function () {
            reconnectAttempts = 0;
            setConnStatus("connected", "Connected");
            $sendBtn.disabled = false;

            // Send auth with token if available
            if (authToken) {
                ws.send(JSON.stringify({
                    type: "auth",
                    token: authToken,
                }));
            } else {
                // Legacy fallback
                ws.send(JSON.stringify({
                    type: "auth",
                    user_id: settings.userId,
                    user_name: settings.userName,
                }));
            }
        };

        ws.onmessage = function (event) {
            try {
                var data = JSON.parse(event.data);
                handleServerMessage(data);
            } catch (err) {
                // Plain text fallback
                addMessage("assistant", event.data);
            }
        };

        ws.onclose = function () {
            setConnStatus("disconnected", "Disconnected");
            $sendBtn.disabled = true;
            hideTyping();
            scheduleReconnect();
        };

        ws.onerror = function () {
            // onclose will fire after this
        };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
            addSystemMessage("Unable to connect. Check that MyAi is running on port 8001.");
            return;
        }
        reconnectAttempts++;
        reconnectTimer = setTimeout(function () {
            reconnectTimer = null;
            connect();
        }, RECONNECT_DELAY_MS);
    }

    function disconnect() {
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        reconnectAttempts = MAX_RECONNECT_ATTEMPTS; // prevent auto-reconnect
        if (ws) {
            ws.close();
            ws = null;
        }
    }

    // -- Message handling --
    function handleServerMessage(data) {
        switch (data.type) {
            case "response":
                hideTyping();
                addMessage("assistant", data.text, data.agent, data.message_id, data.conversation_id, data.source);
                break;
            case "stream_end":
                hideTyping();
                addMessage("assistant", data.text, data.agent, data.message_id, data.conversation_id, data.source);
                break;
            case "feedback_ack":
                markFeedbackSent(data.message_id, data.rating);
                break;
            case "error":
                hideTyping();
                addErrorMessage(data.text || "An error occurred.");
                break;
            case "auth_error":
                hideTyping();
                addErrorMessage(data.text || "Authentication failed.");
                // Token is invalid, force re-login
                logout();
                break;
            case "status":
                updateStatusPanel(data);
                break;
            case "skills":
                renderSkills(data.skills || []);
                break;
            case "typing":
                showTyping(data.text);
                break;
            case "system":
                addSystemMessage(data.text);
                // Update current user if provided
                if (data.user) {
                    currentUser = data.user;
                    $userDisplayName.textContent = currentUser.display_name || "User";
                    $userRoleBadge.textContent = formatRoleName(currentUser.role_level);
                    $userRoleBadge.className = "user-role-badge role-" + currentUser.role_level;
                }
                break;
            default:
                if (data.text) {
                    hideTyping();
                    addMessage("assistant", data.text, data.agent);
                }
        }
    }

    function sendMessage(text) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (!text.trim()) return;

        addMessage("user", text);
        showTyping();

        ws.send(JSON.stringify({
            type: "message",
            text: text,
            user_id: settings.userId,
            user_name: settings.userName,
        }));
    }

    function doSend() {
        var text = $input.value.trim();
        if (!text) return;
        removeWelcome();
        sendMessage(text);
        $input.value = "";
        autoResize();
    }

    // -- UI rendering --
    function addMessage(role, text, agent, messageId, conversationId, source) {
        var $msg = document.createElement("div");
        $msg.className = "message " + role;

        if (messageId) {
            $msg.setAttribute("data-message-id", messageId);
            $msg.setAttribute("data-conversation-id", conversationId || "");
            $msg.setAttribute("data-source", source || "local");
            $msg.setAttribute("data-agent-name", agent || "");
        }

        var html = "";
        if (agent && role === "assistant") {
            html += '<span class="agent-tag">' + escapeHtml(agent) + '</span>\n';
        }
        html += formatMessage(text);
        html += '<span class="msg-time">' + formatTime() + '</span>';

        // Add feedback buttons for assistant messages
        if (role === "assistant" && messageId) {
            html += '<div class="feedback-buttons" data-msg-id="' + messageId + '">';
            html += '<button class="feedback-btn feedback-up" title="Good response" onclick="window._sendFeedback(' + messageId + ', \'up\', this)">&#x1F44D;</button>';
            html += '<button class="feedback-btn feedback-down" title="Poor response" onclick="window._sendFeedback(' + messageId + ', \'down\', this)">&#x1F44E;</button>';
            html += '</div>';
        }

        $msg.innerHTML = html;
        $messages.appendChild($msg);
        scrollToBottom();
    }

    // Feedback handler (exposed globally for onclick)
    window._sendFeedback = function (messageId, rating, btnEl) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        var $msg = btnEl.closest(".message");
        var convId = $msg ? $msg.getAttribute("data-conversation-id") : "";
        var source = $msg ? $msg.getAttribute("data-source") : "local";
        var agentName = $msg ? $msg.getAttribute("data-agent-name") : null;

        ws.send(JSON.stringify({
            type: "feedback",
            message_id: messageId,
            conversation_id: convId,
            rating: rating,
            source: source,
            agent_name: agentName || undefined,
        }));

        // Disable both buttons and highlight the selected one
        var $container = btnEl.parentElement;
        var buttons = $container.querySelectorAll(".feedback-btn");
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].disabled = true;
            buttons[i].classList.add("feedback-sent");
        }
        btnEl.classList.add("feedback-selected");
    };

    function markFeedbackSent(messageId, rating) {
        var $msg = document.querySelector('.message[data-message-id="' + messageId + '"]');
        if (!$msg) return;
        var $container = $msg.querySelector(".feedback-buttons");
        if (!$container) return;
        var buttons = $container.querySelectorAll(".feedback-btn");
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].disabled = true;
            buttons[i].classList.add("feedback-sent");
        }
        var selected = rating === "up" ? ".feedback-up" : ".feedback-down";
        var $btn = $container.querySelector(selected);
        if ($btn) $btn.classList.add("feedback-selected");
    }

    function addSystemMessage(text) {
        var $msg = document.createElement("div");
        $msg.className = "message system";
        $msg.textContent = text;
        $messages.appendChild($msg);
        scrollToBottom();
    }

    function addErrorMessage(text) {
        var $msg = document.createElement("div");
        $msg.className = "message error";
        $msg.textContent = text;
        $messages.appendChild($msg);
        scrollToBottom();
    }

    function showWelcome() {
        var $welcome = document.createElement("div");
        $welcome.className = "welcome";
        $welcome.id = "welcome";
        var userName = (currentUser && currentUser.display_name) ? currentUser.display_name : "there";
        $welcome.innerHTML =
            '<h2>Welcome, ' + escapeHtml(userName) + '</h2>' +
            '<p>Your personal AI assistant. Ask anything, use commands like <code>/help</code>, ' +
            'or let the enterprise skills handle your requests automatically.</p>';
        $messages.appendChild($welcome);
    }

    function removeWelcome() {
        var $welcome = document.getElementById("welcome");
        if ($welcome) $welcome.remove();
    }

    function showTyping(text) {
        var $typingText = $typing.querySelector(".typing-text");
        $typingText.textContent = text || "MyAi is thinking...";
        $typing.classList.remove("hidden");
        scrollToBottom();
    }

    function hideTyping() {
        $typing.classList.add("hidden");
    }

    function scrollToBottom() {
        requestAnimationFrame(function () {
            $messages.scrollTop = $messages.scrollHeight;
        });
    }

    function setConnStatus(state, text) {
        $connStatus.className = "conn-badge " + state;
        $connStatus.textContent = text;
    }

    function clearChat() {
        $messages.innerHTML = "";
        showWelcome();

        // Also tell the server to clear conversation
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "message",
                text: "/clear",
                user_id: settings.userId,
                user_name: settings.userName,
            }));
        }
    }

    // -- Web Search Toggle --
    function toggleSearch() {
        var isOn = $searchToggle.checked;
        var cmd = isOn ? "/search on" : "/search off";

        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "message",
                text: cmd,
                user_id: settings.userId,
                user_name: settings.userName,
            }));
        }

        $statusSearch.textContent = isOn ? "On" : "Off";
    }

    // -- Status & Skills --
    function fetchStatus() {
        fetch("/api/web/status")
            .then(function (r) { return r.json(); })
            .then(function (data) { updateStatusPanel(data); })
            .catch(function () {});
    }

    function fetchSkills() {
        fetch("/api/web/skills")
            .then(function (r) { return r.json(); })
            .then(function (data) { renderSkills(data.skills || []); })
            .catch(function () {});
    }

    function updateStatusPanel(data) {
        if (data.ollama !== undefined) {
            $statusOllama.textContent = data.ollama ? "Connected" : "Offline";
            $statusOllama.className = "status-badge " + (data.ollama ? "online" : "offline");
        }
        if (data.model) {
            $statusModel.textContent = data.model;
        }
        if (data.graph !== undefined) {
            var graphText = data.graph === true ? "Connected" :
                            data.graph === "configured" ? "Not signed in" : "Not configured";
            var graphClass = data.graph === true ? "online" :
                             data.graph === "configured" ? "partial" : "offline";
            $statusGraph.textContent = graphText;
            $statusGraph.className = "status-badge " + graphClass;
        }
        if (data.search !== undefined) {
            $statusSearch.textContent = data.search ? "On" : "Off";
            if ($searchToggle) {
                $searchToggle.checked = !!data.search;
            }
        }
    }

    function renderSkills(skills) {
        $skillsList.innerHTML = "";
        if (!skills.length) {
            $skillsList.innerHTML = '<div style="color:var(--text-muted);font-size:12px">No skills loaded</div>';
            return;
        }
        for (var i = 0; i < skills.length; i++) {
            var s = skills[i];
            var $item = document.createElement("div");
            $item.className = "skill-item";
            $item.innerHTML = '<span class="skill-name">' + escapeHtml(s.agent) + '</span><span class="skill-desc"> -- ' + escapeHtml(s.description) + '</span>';
            $skillsList.appendChild($item);
        }
    }

    // -- Formatting --
    function formatMessage(text) {
        // Basic markdown-like formatting
        var html = escapeHtml(text);

        // Code blocks (```...```)
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
            return '<pre><code>' + code.trim() + '</code></pre>';
        });

        // Inline code (`...`)
        html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

        // Bold (**...**)
        html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

        // Bold (*...* -- Slack style)
        html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<strong>$1</strong>");

        // Italic (_..._)
        html = html.replace(/(?<!_)_([^_]+)_(?!_)/g, "<em>$1</em>");

        // Links (<url|text> Slack style)
        html = html.replace(/&lt;(https?:\/\/[^|&]+)\|([^&]+)&gt;/g,
            '<a href="$1" target="_blank" rel="noopener" style="color:var(--accent)">$2</a>');

        // Plain URLs
        html = html.replace(/(https?:\/\/[^\s<]+)/g,
            '<a href="$1" target="_blank" rel="noopener" style="color:var(--accent)">$1</a>');

        return html;
    }

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    function formatTime() {
        return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    // -- Events --
    function bindAuthEvents() {
        $setupForm.addEventListener("submit", setupAdmin);
        $loginForm.addEventListener("submit", login);
        if ($logoutBtn) {
            $logoutBtn.addEventListener("click", logout);
        }
    }

    function bindChatEvents() {
        // Prevent default form submission entirely
        $form.addEventListener("submit", function (e) {
            e.preventDefault();
            e.stopPropagation();
            return false;
        });

        // Send button click
        $sendBtn.addEventListener("click", function (e) {
            e.preventDefault();
            doSend();
        });

        // Enter key sends, Shift+Enter adds new line
        $input.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.stopPropagation();
                doSend();
            }
        });

        $input.addEventListener("input", autoResize);

        $toggleSidebar.addEventListener("click", function () {
            $sidebar.classList.toggle("collapsed");
        });

        $clearBtn.addEventListener("click", clearChat);

        // Web search toggle
        if ($searchToggle) {
            $searchToggle.addEventListener("change", toggleSearch);
        }

        $closeSettings.addEventListener("click", function () {
            $settingsModal.classList.add("hidden");
        });

        $settingsModal.addEventListener("click", function (e) {
            if (e.target === $settingsModal) {
                $settingsModal.classList.add("hidden");
            }
        });

        $saveSettings.addEventListener("click", function () {
            settings.userId = $settingUserId.value.trim() || settings.userId;
            settings.userName = $settingUserName.value.trim() || settings.userName;
            settings.wsUrl = $settingWsUrl.value.trim();
            saveSettings();
            $settingsModal.classList.add("hidden");
            addSystemMessage("Settings saved. Reconnecting...");
            disconnect();
            reconnectAttempts = 0;
            setTimeout(connect, 500);
        });
    }

    function autoResize() {
        $input.style.height = "auto";
        $input.style.height = Math.min($input.scrollHeight, 150) + "px";
    }

    // -- Start --
    init();
})();
