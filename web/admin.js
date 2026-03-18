/**
 * MyAi Super Admin Dashboard JavaScript
 *
 * Handles data fetching, rendering, tab switching, and auto-refresh
 * for the admin dashboard. No external dependencies.
 */

(function () {
    "use strict";

    // ── Configuration ──

    var REFRESH_INTERVAL_MS = 60000; // Auto-refresh every 60 seconds
    var refreshTimer = null;
    var currentTab = "overview";

    // ── Auth Helpers ──

    function getToken() {
        return localStorage.getItem("myai_token") || "";
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
                    '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#f87171;font-size:18px;">' +
                    'Access Denied: Admin privileges required. <a href="/" style="color:#6c63ff;margin-left:12px;">Return to Chat</a></div>';
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

    // ── Initialization ──

    function init() {
        var token = getToken();
        if (!token) {
            window.location.href = "/";
            return;
        }

        var userInfo = getUserInfo();
        var userEl = document.getElementById("user-info");
        if (userInfo && userEl) {
            userEl.textContent = (userInfo.display_name || userInfo.email || "Admin");
        }

        loadCurrentTab();
        startAutoRefresh();
    }

    window.logout = function () {
        localStorage.removeItem("myai_token");
        localStorage.removeItem("myai_user");
        window.location.href = "/";
    };

    // ── Tab Switching ──

    window.switchTab = function (tabName) {
        currentTab = tabName;

        // Update tab buttons
        var buttons = document.querySelectorAll(".tab-btn");
        for (var i = 0; i < buttons.length; i++) {
            if (buttons[i].getAttribute("data-tab") === tabName) {
                buttons[i].classList.add("active");
            } else {
                buttons[i].classList.remove("active");
            }
        }

        // Update tab panels
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
            case "skills":
                loadSkillMetrics();
                break;
            case "users":
                loadUsers();
                break;
            case "datasources":
                loadDataSources();
                break;
            case "errors":
                loadErrors();
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
        fetchWithAuth("/api/admin/analytics/overview?period_hours=24")
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

        // Load volume chart
        fetchWithAuth("/api/admin/analytics/volume?period_hours=168&bucket=hourly")
            .then(function (data) {
                renderVolumeChart(data.volume || []);
            })
            .catch(function () {});

        // Load response time distribution
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
            container.innerHTML = '<div class="empty-state"><p>No volume data available yet.</p></div>';
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
            svg += '<line x1="' + padding.left + '" y1="' + yLine + '" x2="' + (width - padding.right) + '" y2="' + yLine + '" stroke="#2d2d2d" stroke-width="1"/>';
            svg += '<text x="' + (padding.left - 4) + '" y="' + (yLine + 3) + '" text-anchor="end" fill="#666" font-size="9" font-family="monospace">' + yLabel + '</text>';
        }

        // Bars
        for (var b = 0; b < data.length; b++) {
            var barHeight = (data[b].message_count / maxVal) * chartHeight;
            var x = padding.left + (b * (chartWidth / data.length));
            var y = padding.top + chartHeight - barHeight;
            svg += '<rect x="' + x + '" y="' + y + '" width="' + barWidth + '" height="' + barHeight + '" fill="#6c63ff" opacity="0.8" rx="1">';
            svg += '<title>' + (data[b].bucket_time || "") + ': ' + data[b].message_count + ' messages</title>';
            svg += '</rect>';
        }

        // X-axis labels (show a few evenly spaced)
        var labelCount = Math.min(7, data.length);
        var labelStep = Math.max(1, Math.floor(data.length / labelCount));
        for (var l = 0; l < data.length; l += labelStep) {
            var labelX = padding.left + (l * (chartWidth / data.length)) + barWidth / 2;
            var labelText = data[l].bucket_time || "";
            // Shorten label
            if (labelText.length > 10) {
                labelText = labelText.substring(5, 10);
            }
            svg += '<text x="' + labelX + '" y="' + (height - 5) + '" text-anchor="middle" fill="#666" font-size="9" font-family="monospace">' + labelText + '</text>';
        }

        svg += '</svg>';
        container.innerHTML = svg;
    }

    // ── Skills Tab ──

    function loadSkillMetrics() {
        fetchWithAuth("/api/admin/analytics/skills?period_hours=168")
            .then(function (data) {
                var skills = data.skills || [];
                var tbody = document.getElementById("skills-tbody");
                if (!tbody) return;

                if (skills.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="muted" style="text-align:center;padding:32px;">No skill execution data yet.</td></tr>';
                    return;
                }

                var html = "";
                for (var i = 0; i < skills.length; i++) {
                    var s = skills[i];
                    var successBadge = s.success_rate >= 95
                        ? "badge-success"
                        : s.success_rate >= 80
                            ? "badge-warning"
                            : "badge-error";

                    html += "<tr>" +
                        "<td><strong>" + escapeHtml(s.skill_name) + "</strong></td>" +
                        '<td class="mono">' + s.execution_count + "</td>" +
                        '<td class="mono">' + (s.avg_confidence || 0).toFixed(2) + "</td>" +
                        '<td class="mono">' + formatNumber(s.avg_response_time_ms) + "</td>" +
                        '<td><span class="badge ' + successBadge + '">' + s.success_rate + "%</span></td>" +
                        '<td class="mono" style="color:var(--success);">' + s.thumbs_up + "</td>" +
                        '<td class="mono" style="color:var(--error);">' + s.thumbs_down + "</td>" +
                        "</tr>";
                }
                tbody.innerHTML = html;
            })
            .catch(function () {});
    }

    // ── Users Tab ──

    function loadUsers() {
        // Load both user list and activity data
        var usersPromise = fetchWithAuth("/api/admin/users");
        var activityPromise = fetchWithAuth("/api/admin/analytics/users?period_hours=168&limit=100");

        Promise.all([usersPromise, activityPromise])
            .then(function (results) {
                var userList = (results[0] && results[0].users) || [];
                var activityList = (results[1] && results[1].users) || [];

                // Create activity lookup by user_id
                var activityMap = {};
                for (var a = 0; a < activityList.length; a++) {
                    activityMap[activityList[a].user_id] = activityList[a];
                }

                var tbody = document.getElementById("users-tbody");
                if (!tbody) return;

                if (userList.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="muted" style="text-align:center;padding:32px;">No users registered yet.</td></tr>';
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
                alert("Error: " + data.error);
            }
            loadUsers();
        }).catch(function (err) {
            alert("Failed to update role: " + err.message);
        });
    };

    window.toggleUserActive = function (userId, activate) {
        var action = activate ? "activate" : "deactivate";
        var url = "/api/admin/users/" + encodeURIComponent(userId) + "/" + action;
        fetchWithAuthBody(url, "POST", {}).then(function (data) {
            if (data.error) {
                alert("Error: " + data.error);
            }
            loadUsers();
        }).catch(function (err) {
            alert("Failed to " + action + " user: " + err.message);
        });
    };

    // ── Data Sources Tab ──

    var SOURCE_TYPE_LABELS = {
        "local_directory": "Local Directory",
        "sql_database": "SQL Database",
        "sharepoint": "SharePoint",
        "rest_api": "REST API",
    };

    var ROLE_LABELS = {
        "employee": "Employee",
        "manager": "Manager",
        "admin": "Admin",
        "super_admin": "Super Admin",
    };

    function statusBadgeClass(status) {
        switch (status) {
            case "ready": return "badge-success";
            case "indexing": return "badge-info";
            case "error": return "badge-error";
            case "pending":
            default: return "badge-muted";
        }
    }

    function loadDataSources() {
        fetchWithAuth("/api/admin/datasources")
            .then(function (data) {
                var sources = data.datasources || [];
                var tbody = document.getElementById("datasources-tbody");
                if (!tbody) return;

                if (sources.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="muted" style="text-align:center;padding:32px;">No data sources configured yet. Click "Add Data Source" to get started.</td></tr>';
                    return;
                }

                var html = "";
                for (var i = 0; i < sources.length; i++) {
                    var s = sources[i];
                    var typeLabel = SOURCE_TYPE_LABELS[s.source_type] || s.source_type;
                    var roleLabel = ROLE_LABELS[s.min_role_level] || s.min_role_level;
                    var badgeClass = statusBadgeClass(s.status);
                    var lastIndexed = s.last_indexed_at ? formatTimestamp(s.last_indexed_at) : "Never";
                    var sid = escapeAttr(s.id);

                    html += "<tr>" +
                        "<td><strong>" + escapeHtml(s.name) + "</strong></td>" +
                        "<td>" + escapeHtml(typeLabel) + "</td>" +
                        '<td><span class="badge ' + badgeClass + '">' + escapeHtml(s.status) + "</span></td>" +
                        '<td class="mono">' + (s.document_count || 0) + "</td>" +
                        "<td>" + escapeHtml(roleLabel) + "</td>" +
                        '<td class="muted">' + lastIndexed + "</td>" +
                        "<td>" +
                        '<div class="ds-action-group">' +
                        '<button class="btn-action" onclick="testDataSource(\'' + sid + '\')">Test</button>' +
                        '<button class="btn-action btn-primary-sm" onclick="triggerIndexing(\'' + sid + '\')">Re-index</button>' +
                        '<button class="btn-action" onclick="showEditDataSourceForm(\'' + sid + '\')">Edit</button>' +
                        '<button class="btn-action danger" onclick="deleteDataSource(\'' + sid + '\')">Delete</button>' +
                        '</div>' +
                        "</td>" +
                        "</tr>";
                }
                tbody.innerHTML = html;
            })
            .catch(function () {});
    }

    function showDsToast(message, isError) {
        var toast = document.getElementById("ds-toast");
        if (!toast) return;
        toast.textContent = message;
        toast.className = "ds-toast" + (isError ? " ds-toast-error" : " ds-toast-success");
        setTimeout(function () {
            toast.className = "ds-toast hidden";
        }, 4000);
    }

    window.showAddDataSourceForm = function () {
        document.getElementById("ds-modal-title").textContent = "Add Data Source";
        document.getElementById("ds-edit-id").value = "";
        document.getElementById("ds-name").value = "";
        document.getElementById("ds-type").value = "local_directory";
        document.getElementById("ds-min-role").value = "employee";
        clearDsConfigFields();
        onDsTypeChange();
        document.getElementById("ds-modal").classList.remove("hidden");
    };

    window.showEditDataSourceForm = function (id) {
        fetchWithAuth("/api/admin/datasources/" + encodeURIComponent(id))
            .then(function (data) {
                var ds = data.datasource;
                if (!ds) return;

                document.getElementById("ds-modal-title").textContent = "Edit Data Source";
                document.getElementById("ds-edit-id").value = ds.id;
                document.getElementById("ds-name").value = ds.name || "";
                document.getElementById("ds-type").value = ds.source_type || "local_directory";
                document.getElementById("ds-min-role").value = ds.min_role_level || "employee";

                clearDsConfigFields();
                onDsTypeChange();

                // Populate config fields
                var config = ds.config || {};
                populateDsConfigFields(ds.source_type, config);

                document.getElementById("ds-modal").classList.remove("hidden");
            })
            .catch(function (err) {
                alert("Failed to load data source: " + err.message);
            });
    };

    window.closeDsModal = function () {
        document.getElementById("ds-modal").classList.add("hidden");
    };

    window.onDsTypeChange = function () {
        var type = document.getElementById("ds-type").value;
        var sections = document.querySelectorAll(".ds-config-section");
        for (var i = 0; i < sections.length; i++) {
            if (sections[i].id === "ds-config-" + type) {
                sections[i].classList.remove("hidden");
            } else {
                sections[i].classList.add("hidden");
            }
        }
    };

    function clearDsConfigFields() {
        var fields = [
            "ds-cfg-directory_path",
            "ds-cfg-connection_string", "ds-cfg-query",
            "ds-cfg-client_id", "ds-cfg-client_secret", "ds-cfg-tenant_id", "ds-cfg-site_id", "ds-cfg-folder_path",
            "ds-cfg-base_url", "ds-cfg-auth_config", "ds-cfg-endpoints", "ds-cfg-response_text_field",
        ];
        for (var i = 0; i < fields.length; i++) {
            var el = document.getElementById(fields[i]);
            if (el) el.value = "";
        }
        var authTypeEl = document.getElementById("ds-cfg-auth_type");
        if (authTypeEl) authTypeEl.value = "api_key";
    }

    function populateDsConfigFields(sourceType, config) {
        if (sourceType === "local_directory") {
            setVal("ds-cfg-directory_path", config.directory_path);
        } else if (sourceType === "sql_database") {
            setVal("ds-cfg-connection_string", config.connection_string);
            setVal("ds-cfg-query", config.query);
        } else if (sourceType === "sharepoint") {
            setVal("ds-cfg-client_id", config.client_id);
            setVal("ds-cfg-client_secret", config.client_secret);
            setVal("ds-cfg-tenant_id", config.tenant_id);
            setVal("ds-cfg-site_id", config.site_id);
            setVal("ds-cfg-folder_path", config.folder_path);
        } else if (sourceType === "rest_api") {
            setVal("ds-cfg-base_url", config.base_url);
            setVal("ds-cfg-auth_type", config.auth_type || "api_key");
            setVal("ds-cfg-auth_config", config.auth_config ? JSON.stringify(config.auth_config, null, 2) : "");
            setVal("ds-cfg-endpoints", Array.isArray(config.endpoints) ? config.endpoints.join("\n") : "");
            setVal("ds-cfg-response_text_field", config.response_text_field);
        }
    }

    function setVal(id, value) {
        var el = document.getElementById(id);
        if (el && value !== undefined && value !== null) el.value = value;
    }

    function buildConfigFromForm(sourceType) {
        var config = {};
        if (sourceType === "local_directory") {
            config.directory_path = document.getElementById("ds-cfg-directory_path").value.trim();
        } else if (sourceType === "sql_database") {
            config.connection_string = document.getElementById("ds-cfg-connection_string").value.trim();
            config.query = document.getElementById("ds-cfg-query").value.trim();
        } else if (sourceType === "sharepoint") {
            config.client_id = document.getElementById("ds-cfg-client_id").value.trim();
            config.client_secret = document.getElementById("ds-cfg-client_secret").value.trim();
            config.tenant_id = document.getElementById("ds-cfg-tenant_id").value.trim();
            config.site_id = document.getElementById("ds-cfg-site_id").value.trim();
            config.folder_path = document.getElementById("ds-cfg-folder_path").value.trim();
        } else if (sourceType === "rest_api") {
            config.base_url = document.getElementById("ds-cfg-base_url").value.trim();
            config.auth_type = document.getElementById("ds-cfg-auth_type").value;
            var authConfigRaw = document.getElementById("ds-cfg-auth_config").value.trim();
            if (authConfigRaw) {
                try {
                    config.auth_config = JSON.parse(authConfigRaw);
                } catch (e) {
                    throw new Error("Auth Config must be valid JSON");
                }
            } else {
                config.auth_config = {};
            }
            var endpointsRaw = document.getElementById("ds-cfg-endpoints").value.trim();
            config.endpoints = endpointsRaw ? endpointsRaw.split("\n").map(function (l) { return l.trim(); }).filter(Boolean) : [];
            config.response_text_field = document.getElementById("ds-cfg-response_text_field").value.trim();
        }
        return config;
    }

    window.saveDataSource = function () {
        var editId = document.getElementById("ds-edit-id").value;
        var name = document.getElementById("ds-name").value.trim();
        var sourceType = document.getElementById("ds-type").value;
        var minRole = document.getElementById("ds-min-role").value;

        if (!name) {
            alert("Name is required.");
            return;
        }

        var config;
        try {
            config = buildConfigFromForm(sourceType);
        } catch (e) {
            alert(e.message);
            return;
        }

        var payload = {
            name: name,
            source_type: sourceType,
            config: config,
            min_role_level: minRole,
        };

        var url, method;
        if (editId) {
            url = "/api/admin/datasources/" + encodeURIComponent(editId);
            method = "PUT";
        } else {
            url = "/api/admin/datasources";
            method = "POST";
        }

        fetchWithAuthBody(url, method, payload)
            .then(function (data) {
                if (data.error) {
                    alert("Error: " + data.error);
                    return;
                }
                closeDsModal();
                showDsToast(editId ? "Data source updated." : "Data source created.", false);
                loadDataSources();
            })
            .catch(function (err) {
                alert("Failed to save data source: " + err.message);
            });
    };

    window.deleteDataSource = function (id) {
        if (!confirm("Are you sure you want to delete this data source? This will also remove all indexed documents.")) {
            return;
        }

        fetchWithAuthBody("/api/admin/datasources/" + encodeURIComponent(id), "DELETE", {})
            .then(function (data) {
                if (data.error) {
                    alert("Error: " + data.error);
                    return;
                }
                showDsToast("Data source deleted.", false);
                loadDataSources();
            })
            .catch(function (err) {
                alert("Failed to delete data source: " + err.message);
            });
    };

    window.testDataSource = function (id) {
        showDsToast("Testing connection...", false);
        fetchWithAuthBody("/api/admin/datasources/" + encodeURIComponent(id) + "/test", "POST", {})
            .then(function (data) {
                if (data.error) {
                    showDsToast("Test failed: " + data.error, true);
                    return;
                }
                if (data.success) {
                    showDsToast("Connection successful: " + (data.message || "OK"), false);
                } else {
                    showDsToast("Connection failed: " + (data.message || "Unknown error"), true);
                }
            })
            .catch(function (err) {
                showDsToast("Test error: " + err.message, true);
            });
    };

    window.triggerIndexing = function (id) {
        showDsToast("Starting indexing...", false);
        fetchWithAuthBody("/api/admin/datasources/" + encodeURIComponent(id) + "/index", "POST", {})
            .then(function (data) {
                if (data.error) {
                    showDsToast("Indexing failed: " + data.error, true);
                    return;
                }
                showDsToast("Indexing started. Status: " + (data.indexing_status || "in progress"), false);
                // Refresh the table after a short delay to show updated status
                setTimeout(loadDataSources, 2000);
            })
            .catch(function (err) {
                showDsToast("Indexing error: " + err.message, true);
            });
    };

    // ── Errors Tab ──

    function loadErrors() {
        fetchWithAuth("/api/admin/analytics/errors?limit=50")
            .then(function (data) {
                var errors = data.errors || [];
                var tbody = document.getElementById("errors-tbody");
                if (!tbody) return;

                if (errors.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:32px;">No errors recorded. System is healthy.</td></tr>';
                    return;
                }

                var html = "";
                for (var i = 0; i < errors.length; i++) {
                    var e = errors[i];
                    html += "<tr>" +
                        '<td class="mono muted">' + formatTimestamp(e.timestamp) + "</td>" +
                        "<td>" + escapeHtml(e.user_id || "--") + "</td>" +
                        '<td><span class="badge badge-error">' + escapeHtml(e.event_type) + "</span></td>" +
                        "<td>" + escapeHtml(e.skill_name || "--") + "</td>" +
                        '<td class="error-message-cell" title="' + escapeAttr(e.error_message) + '">' + escapeHtml(e.error_message || "--") + "</td>" +
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
