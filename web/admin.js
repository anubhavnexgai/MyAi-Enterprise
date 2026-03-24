/**
 * MyAi Admin Dashboard JavaScript — Obsidian Dark Theme
 *
 * Handles data fetching, rendering, tab switching, auto-refresh,
 * and the Learning Loop tab (feedback, refinements, prompt versions).
 * No external dependencies.
 */

(function () {
    "use strict";

    // ── Configuration ──

    var REFRESH_INTERVAL_MS = 60000;
    var refreshTimer = null;
    var currentTab = "overview";

    // ── Auth Helpers ──

    function getToken() {
        return localStorage.getItem("myai_auth_token") || localStorage.getItem("myai_token") || "";
    }

    function getUserInfo() {
        var info = localStorage.getItem("myai_user");
        if (info) {
            try {
                return JSON.parse(info);
            } catch (e) {
                return null;
            }
        }
        return null;
    }

    function fetchWithAuth(url) {
        var token = getToken();
        if (!token) {
            window.location.href = "/";
            return Promise.reject(new Error("Not authenticated"));
        }
        return fetch(url, {
            headers: {
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        }).then(function (resp) {
            if (resp.status === 401) {
                window.location.href = "/";
                return Promise.reject(new Error("Session expired"));
            }
            if (resp.status === 403) {
                document.body.innerHTML =
                    '<div style="display:flex;align-items:center;justify-content:center;height:100vh;' +
                    'font-family:Inter,sans-serif;color:#ff6e84;font-size:18px;background:#060e20;">' +
                    'Access Denied: Admin privileges required. ' +
                    '<a href="/" style="color:#9fa7ff;margin-left:12px;">Return to Chat</a></div>';
                return Promise.reject(new Error("Forbidden"));
            }
            return resp.json();
        });
    }

    function fetchWithAuthBody(url, method, body) {
        var token = getToken();
        if (!token) {
            window.location.href = "/";
            return Promise.reject(new Error("Not authenticated"));
        }
        return fetch(url, {
            method: method,
            headers: {
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
            body: JSON.stringify(body),
        }).then(function (resp) {
            if (resp.status === 401) {
                window.location.href = "/";
                return Promise.reject(new Error("Session expired"));
            }
            return resp.json();
        });
    }

    // ── Toast ──

    function showToast(message, isError) {
        var existing = document.querySelector(".toast");
        if (existing) existing.remove();

        var toast = document.createElement("div");
        toast.className = "toast " + (isError ? "toast-error" : "toast-success");
        toast.textContent = message;
        document.body.appendChild(toast);

        setTimeout(function () {
            if (toast.parentNode) toast.remove();
        }, 4000);
    }

    // ── Initialization ──

    function init() {
        var token = getToken();
        if (!token) {
            window.location.href = "/";
            return;
        }

        var userInfo = getUserInfo();
        var userEl = document.getElementById("user-info");
        if (userEl) {
            if (userInfo) {
                userEl.textContent = (userInfo.display_name || userInfo.email || "Admin");
            } else {
                userEl.textContent = "Admin";
            }
        }

        loadCurrentTab();
        startAutoRefresh();
    }

    window.logout = function () {
        localStorage.removeItem("myai_auth_token");
        localStorage.removeItem("myai_token");
        localStorage.removeItem("myai_user");
        window.location.href = "/";
    };

    // ── Tab Switching ──

    window.switchTab = function (tabName) {
        currentTab = tabName;

        var buttons = document.querySelectorAll(".tab-btn");
        for (var i = 0; i < buttons.length; i++) {
            if (buttons[i].getAttribute("data-tab") === tabName) {
                buttons[i].classList.add("active");
            } else {
                buttons[i].classList.remove("active");
            }
        }

        var panels = document.querySelectorAll(".tab-panel");
        for (var j = 0; j < panels.length; j++) {
            if (panels[j].id === "panel-" + tabName) {
                panels[j].classList.add("active");
            } else {
                panels[j].classList.remove("active");
            }
        }

        loadCurrentTab();
    };

    function loadCurrentTab() {
        switch (currentTab) {
            case "overview":
                loadOverview();
                break;
            case "users":
                loadUsers();
                break;
            case "learning":
                loadLearning();
                break;
            case "system":
                loadSystemHealth();
                break;
        }
    }

    // ── Auto-Refresh ──

    function startAutoRefresh() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = setInterval(loadCurrentTab, REFRESH_INTERVAL_MS);
    }

    // ── Overview Tab ──

    function loadOverview() {
        fetchWithAuth("/api/admin/analytics/overview?period_hours=8760")
            .then(function (data) {
                setText("metric-messages", formatNumber(data.total_messages));
                setText("metric-active-users", formatNumber(data.active_users));
                setText("metric-skill-execs", formatNumber(data.total_skill_executions));
                setText("metric-avg-response", formatNumber(data.avg_response_time_ms));
                setText("metric-error-rate", data.error_rate + "%");
                setText("metric-error-count", data.error_count + " errors");
                setText("metric-conversations", formatNumber(data.total_conversations));
            })
            .catch(function () {});

        fetchWithAuth("/api/admin/analytics/volume?period_hours=168&bucket=hourly")
            .then(function (data) {
                renderVolumeChart(data.volume || []);
            })
            .catch(function () {});

        fetchWithAuth("/api/admin/analytics/response-times?period_hours=168")
            .then(function (data) {
                renderResponseTimes(data);
            })
            .catch(function () {});
    }

    function renderResponseTimes(data) {
        var container = document.getElementById("response-times");
        if (!container) return;

        var items = [
            { label: "P50", value: data.p50 },
            { label: "P75", value: data.p75 },
            { label: "P90", value: data.p90 },
            { label: "P95", value: data.p95 },
            { label: "P99", value: data.p99 },
            { label: "Avg", value: data.avg },
            { label: "Min", value: data.min },
            { label: "Max", value: data.max },
        ];

        var html = "";
        for (var i = 0; i < items.length; i++) {
            html +=
                '<div class="percentile-item">' +
                '<div class="percentile-label">' + items[i].label + '</div>' +
                '<div class="percentile-value">' + formatNumber(items[i].value) + '</div>' +
                '<div class="percentile-unit">ms</div>' +
                '</div>';
        }

        container.innerHTML = html;
    }

    // ── Volume Chart (SVG Bar Chart) ──

    function renderVolumeChart(data) {
        var container = document.getElementById("volume-chart");
        if (!container) return;

        if (!data || data.length === 0) {
            container.innerHTML = '<div class="empty-state"><span class="material-symbols-outlined">bar_chart</span><p>No volume data available yet.</p></div>';
            return;
        }

        var width = container.clientWidth || 800;
        var height = 200;
        var padding = { top: 10, right: 10, bottom: 30, left: 40 };
        var chartWidth = width - padding.left - padding.right;
        var chartHeight = height - padding.top - padding.bottom;

        var maxVal = 0;
        for (var i = 0; i < data.length; i++) {
            if (data[i].message_count > maxVal) maxVal = data[i].message_count;
        }
        if (maxVal === 0) maxVal = 1;

        var barWidth = Math.max(1, Math.floor(chartWidth / data.length) - 1);

        var svg = '<svg class="chart-svg" viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="xMidYMid meet">';

        // Y-axis gridlines
        for (var g = 0; g <= 4; g++) {
            var yLine = padding.top + chartHeight - (chartHeight * g / 4);
            var yLabel = Math.round(maxVal * g / 4);
            svg += '<line x1="' + padding.left + '" y1="' + yLine + '" x2="' + (width - padding.right) + '" y2="' + yLine + '" stroke="rgba(64,72,93,0.3)" stroke-width="1"/>';
            svg += '<text x="' + (padding.left - 4) + '" y="' + (yLine + 3) + '" text-anchor="end" fill="#6d758c" font-size="9" font-family="Inter, sans-serif">' + yLabel + '</text>';
        }

        // Bars with gradient effect
        svg += '<defs><linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#9fa7ff" stop-opacity="0.9"/>' +
            '<stop offset="100%" stop-color="#9fa7ff" stop-opacity="0.3"/>' +
            '</linearGradient></defs>';

        for (var b = 0; b < data.length; b++) {
            var barHeight = (data[b].message_count / maxVal) * chartHeight;
            var x = padding.left + (b * (chartWidth / data.length));
            var y = padding.top + chartHeight - barHeight;
            svg += '<rect x="' + x + '" y="' + y + '" width="' + barWidth + '" height="' + barHeight + '" fill="url(#barGrad)" rx="2">';
            svg += '<title>' + (data[b].bucket_time || "") + ': ' + data[b].message_count + ' messages</title>';
            svg += '</rect>';
        }

        // X-axis labels
        var labelCount = Math.min(7, data.length);
        var labelStep = Math.max(1, Math.floor(data.length / labelCount));
        for (var l = 0; l < data.length; l += labelStep) {
            var labelX = padding.left + (l * (chartWidth / data.length)) + barWidth / 2;
            var labelText = data[l].bucket_time || "";
            if (labelText.length > 10) {
                labelText = labelText.substring(5, 10);
            }
            svg += '<text x="' + labelX + '" y="' + (height - 5) + '" text-anchor="middle" fill="#6d758c" font-size="9" font-family="Inter, sans-serif">' + labelText + '</text>';
        }

        svg += '</svg>';
        container.innerHTML = svg;
    }

    // ── Users Tab ──

    function loadUsers() {
        var usersPromise = fetchWithAuth("/api/admin/users");
        var activityPromise = fetchWithAuth("/api/admin/analytics/users?period_hours=168&limit=100");

        Promise.all([usersPromise, activityPromise])
            .then(function (results) {
                var userList = (results[0] && results[0].users) || [];
                var activityList = (results[1] && results[1].users) || [];

                var activityMap = {};
                for (var a = 0; a < activityList.length; a++) {
                    activityMap[activityList[a].user_id] = activityList[a];
                }

                var tbody = document.getElementById("users-tbody");
                if (!tbody) return;

                if (userList.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="muted center-cell">No users registered yet.</td></tr>';
                    return;
                }

                var html = "";
                for (var i = 0; i < userList.length; i++) {
                    var u = userList[i];
                    var activity = activityMap[u.id] || {};
                    var msgCount = activity.message_count || 0;
                    var lastActive = activity.last_active || u.last_login_at || u.created_at || "--";
                    var isActive = u.is_active !== false;
                    var statusBadge = isActive
                        ? '<span class="badge badge-success">Active</span>'
                        : '<span class="badge badge-error">Inactive</span>';

                    var roleOptions = buildRoleOptions(u.role_level);

                    var actionBtn = isActive
                        ? '<button class="btn-action danger" onclick="toggleUserActive(\'' + escapeAttr(u.id) + '\', false)">Deactivate</button>'
                        : '<button class="btn-action success" onclick="toggleUserActive(\'' + escapeAttr(u.id) + '\', true)">Activate</button>';

                    html += "<tr>" +
                        "<td><strong>" + escapeHtml(u.display_name || u.id) + "</strong></td>" +
                        "<td>" + escapeHtml(u.email || "") + "</td>" +
                        '<td><select class="role-select" onchange="changeUserRole(\'' + escapeAttr(u.id) + '\', this.value)">' + roleOptions + "</select></td>" +
                        '<td class="mono">' + msgCount + "</td>" +
                        '<td class="muted">' + formatTimestamp(lastActive) + "</td>" +
                        "<td>" + statusBadge + "</td>" +
                        "<td>" + actionBtn + "</td>" +
                        "</tr>";
                }
                tbody.innerHTML = html;
            })
            .catch(function () {});
    }

    function buildRoleOptions(currentRole) {
        var roles = ["super_admin", "admin", "manager", "employee"];
        var labels = {
            "super_admin": "Super Admin",
            "admin": "Admin",
            "manager": "Manager",
            "employee": "Employee",
        };
        var html = "";
        for (var i = 0; i < roles.length; i++) {
            var selected = roles[i] === currentRole ? " selected" : "";
            html += '<option value="' + roles[i] + '"' + selected + '>' + labels[roles[i]] + '</option>';
        }
        return html;
    }

    window.changeUserRole = function (userId, newRole) {
        fetchWithAuthBody("/api/admin/users/" + encodeURIComponent(userId) + "/role", "PUT", {
            role_level: newRole,
        }).then(function (data) {
            if (data.error) {
                showToast("Error: " + data.error, true);
            } else {
                showToast("Role updated successfully", false);
            }
            loadUsers();
        }).catch(function (err) {
            showToast("Failed to update role: " + err.message, true);
        });
    };

    window.toggleUserActive = function (userId, activate) {
        var action = activate ? "activate" : "deactivate";
        var url = "/api/admin/users/" + encodeURIComponent(userId) + "/" + action;
        fetchWithAuthBody(url, "POST", {}).then(function (data) {
            if (data.error) {
                showToast("Error: " + data.error, true);
            } else {
                showToast("User " + action + "d successfully", false);
            }
            loadUsers();
        }).catch(function (err) {
            showToast("Failed to " + action + " user: " + err.message, true);
        });
    };

    // ── Learning Tab ──

    function loadLearning() {
        loadFeedbackStats();
        loadSatisfactionTrend();
        loadPendingEntries();
        loadApprovalHistory();
        loadPromptVersions();
    }

    function loadFeedbackStats() {
        fetchWithAuth("/api/feedback/stats?period_hours=720")
            .then(function (data) {
                var up = data.thumbs_up || 0;
                var down = data.thumbs_down || 0;
                var total = up + down;
                var satisfaction = total > 0 ? Math.round((up / total) * 100) : 0;

                setText("learning-thumbs-up", formatNumber(up));
                setText("learning-thumbs-down", formatNumber(down));
                setText("learning-satisfaction", satisfaction + "%");
            })
            .catch(function () {});

        fetchWithAuth("/api/admin/learning/pending")
            .then(function (data) {
                var entries = data.entries || [];
                setText("learning-pending-count", String(entries.length));
            })
            .catch(function () {
                setText("learning-pending-count", "0");
            });
    }

    function loadSatisfactionTrend() {
        fetchWithAuth("/api/admin/learning/satisfaction-trend?days=30")
            .then(function (data) {
                renderSatisfactionChart(data.trend || []);
            })
            .catch(function () {
                var container = document.getElementById("satisfaction-chart");
                if (container) {
                    container.innerHTML = '<div class="empty-state"><span class="material-symbols-outlined">trending_up</span><p>No trend data available yet.</p></div>';
                }
            });
    }

    function renderSatisfactionChart(data) {
        var container = document.getElementById("satisfaction-chart");
        if (!container) return;

        if (!data || data.length === 0) {
            container.innerHTML = '<div class="empty-state"><span class="material-symbols-outlined">trending_up</span><p>No satisfaction trend data yet. Feedback will appear here.</p></div>';
            return;
        }

        var width = container.clientWidth || 800;
        var height = 180;
        var padding = { top: 10, right: 10, bottom: 30, left: 40 };
        var chartWidth = width - padding.left - padding.right;
        var chartHeight = height - padding.top - padding.bottom;

        // Find max for scaling (satisfaction is 0-100)
        var maxVal = 100;

        var svg = '<svg class="chart-svg" viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="xMidYMid meet">';

        // Y-axis gridlines
        for (var g = 0; g <= 4; g++) {
            var yLine = padding.top + chartHeight - (chartHeight * g / 4);
            var yLabel = Math.round(maxVal * g / 4) + "%";
            svg += '<line x1="' + padding.left + '" y1="' + yLine + '" x2="' + (width - padding.right) + '" y2="' + yLine + '" stroke="rgba(64,72,93,0.3)" stroke-width="1"/>';
            svg += '<text x="' + (padding.left - 4) + '" y="' + (yLine + 3) + '" text-anchor="end" fill="#6d758c" font-size="9" font-family="Inter, sans-serif">' + yLabel + '</text>';
        }

        // Build line path
        var points = [];
        for (var i = 0; i < data.length; i++) {
            var satisfaction = data[i].satisfaction_rate || 0;
            var px = padding.left + (i / Math.max(1, data.length - 1)) * chartWidth;
            var py = padding.top + chartHeight - (satisfaction / maxVal) * chartHeight;
            points.push(px + "," + py);
        }

        if (points.length > 1) {
            // Area fill
            var areaPath = "M" + points[0];
            for (var a = 1; a < points.length; a++) {
                areaPath += " L" + points[a];
            }
            var lastX = padding.left + ((data.length - 1) / Math.max(1, data.length - 1)) * chartWidth;
            var baseY = padding.top + chartHeight;
            areaPath += " L" + lastX + "," + baseY + " L" + padding.left + "," + baseY + " Z";

            svg += '<defs><linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">' +
                '<stop offset="0%" stop-color="#9bffce" stop-opacity="0.2"/>' +
                '<stop offset="100%" stop-color="#9bffce" stop-opacity="0.02"/>' +
                '</linearGradient></defs>';
            svg += '<path d="' + areaPath + '" fill="url(#areaGrad)"/>';

            // Line
            var linePath = "M" + points.join(" L");
            svg += '<path d="' + linePath + '" fill="none" stroke="#9bffce" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';
        }

        // Data points
        for (var d = 0; d < data.length; d++) {
            var sat = data[d].satisfaction_rate || 0;
            var dotX = padding.left + (d / Math.max(1, data.length - 1)) * chartWidth;
            var dotY = padding.top + chartHeight - (sat / maxVal) * chartHeight;
            svg += '<circle cx="' + dotX + '" cy="' + dotY + '" r="3" fill="#9bffce" stroke="#060e20" stroke-width="1.5">';
            svg += '<title>' + (data[d].date || "") + ': ' + sat.toFixed(1) + '%</title>';
            svg += '</circle>';
        }

        // X-axis labels
        var labelCount = Math.min(7, data.length);
        var labelStep = Math.max(1, Math.floor(data.length / labelCount));
        for (var l = 0; l < data.length; l += labelStep) {
            var lx = padding.left + (l / Math.max(1, data.length - 1)) * chartWidth;
            var dateLabel = data[l].date || "";
            if (dateLabel.length > 10) dateLabel = dateLabel.substring(5, 10);
            svg += '<text x="' + lx + '" y="' + (height - 5) + '" text-anchor="middle" fill="#6d758c" font-size="9" font-family="Inter, sans-serif">' + dateLabel + '</text>';
        }

        svg += '</svg>';
        container.innerHTML = svg;
    }

    function loadPendingEntries() {
        fetchWithAuth("/api/admin/learning/pending")
            .then(function (data) {
                var entries = data.entries || [];
                var container = document.getElementById("pending-entries");
                if (!container) return;

                if (entries.length === 0) {
                    container.innerHTML = '<div class="empty-state"><span class="material-symbols-outlined">check_circle</span><p>No pending refinements. All caught up!</p></div>';
                    return;
                }

                var html = "";
                for (var i = 0; i < entries.length; i++) {
                    var e = entries[i];
                    var typeLabel = (e.entry_type || "unknown").replace(/_/g, " ");
                    var created = formatTimestamp(e.created_at);

                    html += '<div class="entry-card">';
                    html += '<div class="entry-header">';
                    html += '<span class="entry-type">' + escapeHtml(typeLabel) + '</span>';
                    html += '<span class="entry-meta">Created ' + created + '</span>';
                    html += '</div>';

                    if (e.trigger_feedback) {
                        html += '<div class="entry-trigger"><strong>Trigger</strong>' + escapeHtml(e.trigger_feedback) + '</div>';
                    }

                    if (e.suggested_improvement) {
                        html += '<div class="entry-improvement"><strong>Suggested Improvement</strong>' + escapeHtml(e.suggested_improvement) + '</div>';
                    }

                    html += '<div class="entry-actions">';
                    html += '<button class="btn-action btn-primary" onclick="approveEntry(\'' + escapeAttr(e.id) + '\')">Approve</button>';
                    html += '<button class="btn-action danger" onclick="rejectEntry(\'' + escapeAttr(e.id) + '\')">Reject</button>';
                    html += '</div>';
                    html += '</div>';
                }

                container.innerHTML = html;
            })
            .catch(function () {});
    }

    window.approveEntry = function (entryId) {
        fetchWithAuthBody("/api/admin/learning/" + encodeURIComponent(entryId) + "/approve", "POST", {})
            .then(function (data) {
                if (data.error) {
                    showToast("Error: " + data.error, true);
                } else {
                    showToast("Entry approved and applied", false);
                    loadLearning();
                }
            })
            .catch(function (err) {
                showToast("Failed to approve: " + err.message, true);
            });
    };

    window.rejectEntry = function (entryId) {
        fetchWithAuthBody("/api/admin/learning/" + encodeURIComponent(entryId) + "/reject", "POST", {})
            .then(function (data) {
                if (data.error) {
                    showToast("Error: " + data.error, true);
                } else {
                    showToast("Entry rejected", false);
                    loadLearning();
                }
            })
            .catch(function (err) {
                showToast("Failed to reject: " + err.message, true);
            });
    };

    function loadApprovalHistory() {
        fetchWithAuth("/api/admin/learning/history?limit=50")
            .then(function (data) {
                var entries = data.entries || [];
                var tbody = document.getElementById("history-tbody");
                if (!tbody) return;

                // Filter to only show approved/rejected entries
                var reviewed = [];
                for (var i = 0; i < entries.length; i++) {
                    if (entries[i].status === "approved" || entries[i].status === "rejected") {
                        reviewed.push(entries[i]);
                    }
                }

                if (reviewed.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" class="muted center-cell">No approval history yet.</td></tr>';
                    return;
                }

                var html = "";
                for (var j = 0; j < reviewed.length; j++) {
                    var e = reviewed[j];
                    var typeLabel = (e.entry_type || "unknown").replace(/_/g, " ");
                    var statusClass = e.status === "approved" ? "badge-approved" : "badge-rejected";
                    var trigger = e.trigger_feedback || "--";
                    var improvement = e.suggested_improvement || "--";
                    var reviewer = e.reviewed_by || "--";
                    var reviewedAt = formatTimestamp(e.reviewed_at);

                    // Truncate long text
                    if (trigger.length > 80) trigger = trigger.substring(0, 80) + "...";
                    if (improvement.length > 80) improvement = improvement.substring(0, 80) + "...";

                    html += "<tr>" +
                        "<td>" + escapeHtml(typeLabel) + "</td>" +
                        '<td><span class="badge ' + statusClass + '">' + escapeHtml(e.status) + "</span></td>" +
                        '<td class="muted" title="' + escapeAttr(e.trigger_feedback || "") + '">' + escapeHtml(trigger) + "</td>" +
                        '<td title="' + escapeAttr(e.suggested_improvement || "") + '">' + escapeHtml(improvement) + "</td>" +
                        '<td class="muted">' + escapeHtml(reviewer) + "</td>" +
                        '<td class="muted">' + reviewedAt + "</td>" +
                        "</tr>";
                }
                tbody.innerHTML = html;
            })
            .catch(function () {});
    }

    function loadPromptVersions() {
        fetchWithAuth("/api/admin/learning/prompt-versions")
            .then(function (data) {
                var versions = data.versions || [];
                var tbody = document.getElementById("prompt-versions-tbody");
                if (!tbody) return;

                if (versions.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" class="muted center-cell">No prompt versions recorded yet.</td></tr>';
                    return;
                }

                var html = "";
                for (var i = 0; i < versions.length; i++) {
                    var v = versions[i];
                    var versionNum = versions.length - i;
                    var isActive = v.is_active || (i === 0);
                    var activeBadge = isActive
                        ? '<span class="badge badge-success">Active</span>'
                        : '<span class="badge badge-muted">Previous</span>';

                    var preview = v.prompt_text || "";
                    if (preview.length > 100) preview = preview.substring(0, 100) + "...";

                    html += "<tr>" +
                        '<td class="mono">v' + versionNum + "</td>" +
                        "<td>" + escapeHtml(v.source || "local") + "</td>" +
                        '<td class="muted">' + escapeHtml(v.created_by || "--") + "</td>" +
                        '<td class="muted">' + formatTimestamp(v.created_at) + "</td>" +
                        "<td>" + activeBadge + "</td>" +
                        '<td><span class="prompt-preview" title="' + escapeAttr(v.prompt_text || "") + '">' + escapeHtml(preview) + "</span></td>" +
                        "</tr>";
                }
                tbody.innerHTML = html;
            })
            .catch(function () {});
    }

    // ── System Tab ──

    function loadSystemHealth() {
        fetchWithAuth("/api/admin/analytics/health")
            .then(function (data) {
                var container = document.getElementById("system-health");
                if (!container) return;

                var uptimeStr = formatUptime(data.uptime_seconds || 0);
                var dbSizeStr = formatBytes(data.db_size_bytes || 0);

                container.innerHTML =
                    '<div class="health-card">' +
                    '  <h3>Server</h3>' +
                    '  <div class="health-row"><span class="health-label">Uptime</span><span class="health-value">' + uptimeStr + '</span></div>' +
                    '  <div class="health-row"><span class="health-label">Database Size</span><span class="health-value">' + dbSizeStr + '</span></div>' +
                    '</div>' +

                    '<div class="health-card">' +
                    '  <h3>Data Counts</h3>' +
                    '  <div class="health-row"><span class="health-label">Total Users</span><span class="health-value">' + formatNumber(data.total_users || 0) + '</span></div>' +
                    '  <div class="health-row"><span class="health-label">Total Conversations</span><span class="health-value">' + formatNumber(data.total_conversations || 0) + '</span></div>' +
                    '  <div class="health-row"><span class="health-label">Total Messages</span><span class="health-value">' + formatNumber(data.total_messages || 0) + '</span></div>' +
                    '</div>' +

                    '<div class="health-card">' +
                    '  <h3>Vector Store</h3>' +
                    '  <div class="health-row"><span class="health-label">ChromaDB Indexes</span><span class="health-value">' + formatNumber(data.index_count || 0) + '</span></div>' +
                    '</div>';
            })
            .catch(function () {});
    }

    // ── Utility Functions ──

    function setText(id, text) {
        var el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    function formatNumber(n) {
        if (n === null || n === undefined) return "0";
        if (typeof n === "number" && n >= 1000) {
            return n.toLocaleString();
        }
        return String(n);
    }

    function formatTimestamp(ts) {
        if (!ts || ts === "--") return "--";
        try {
            var d = new Date(ts);
            if (isNaN(d.getTime())) return ts;
            var now = new Date();
            var diffMs = now - d;
            var diffMins = Math.floor(diffMs / 60000);
            if (diffMins < 1) return "just now";
            if (diffMins < 60) return diffMins + "m ago";
            var diffHours = Math.floor(diffMins / 60);
            if (diffHours < 24) return diffHours + "h ago";
            var diffDays = Math.floor(diffHours / 24);
            if (diffDays < 7) return diffDays + "d ago";
            return d.toLocaleDateString();
        } catch (e) {
            return ts;
        }
    }

    function formatUptime(seconds) {
        var days = Math.floor(seconds / 86400);
        var hours = Math.floor((seconds % 86400) / 3600);
        var mins = Math.floor((seconds % 3600) / 60);
        if (days > 0) return days + "d " + hours + "h " + mins + "m";
        if (hours > 0) return hours + "h " + mins + "m";
        return mins + "m";
    }

    function formatBytes(bytes) {
        if (bytes === 0) return "0 B";
        var units = ["B", "KB", "MB", "GB"];
        var i = 0;
        var size = bytes;
        while (size >= 1024 && i < units.length - 1) {
            size /= 1024;
            i++;
        }
        return size.toFixed(i > 0 ? 1 : 0) + " " + units[i];
    }

    function escapeHtml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function escapeAttr(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    // ── Start ──

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

})();
