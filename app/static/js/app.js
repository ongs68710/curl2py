let groups = [
    { id: "common", name: "常用工具", icon: "★", tools: [
        ["curl-python", "curl 转 Python", "将浏览器复制的 curl 转换为 requests 代码", "#/curl/python"],
        ["curl-feapder", "curl 转 Feapder", "生成适合爬虫项目使用的 Feapder 模板", "#/curl/feapder"],
        ["json-diff", "JSON 对比", "查看 JSON 字段新增、删除和修改", "#/json/diff"],
        ["timestamp", "时间转换", "时间戳与日期时间互相转换", "#/time"],
    ]},
    { id: "cookie", name: "Cookie 管理", icon: "◍", tools: [
        ["cookie-vault", "Cookie 收藏夹", "加密保存常用站点的 Cookie，主密码解锁后可用", "#/vault"],
    ]},
    { id: "format", name: "格式化工具", icon: "▦", tools: [
        ["json-format", "JSON 格式化", "格式化、压缩和校验 JSON", "#/format/json"],
        ["header-format", "Header 格式化", "Header 文本与字典互相转换", "#/format/header"],
        ["cookie-format", "Cookie 格式化", "Cookie 文本转换为字典", "#/format/cookie"],
        ["dict-format", "Dict 格式化", "解析 Python 字典和列表", "#/format/dict"],
        ["javascript-format", "JavaScript 格式化", "整理 JavaScript 文本结构", "#/format/javascript"],
        ["html-format", "HTML 格式化", "整理 HTML 文档结构", "#/format/html"],
    ]},
    { id: "url", name: "URL 工具", icon: "↗", tools: [
        ["url-parse", "URL 参数提取", "提取 URL、路径和查询参数", "#/url/parse"],
        ["url-encode", "URL 编码", "对 URL 或文本进行编码", "#/url/encode"],
        ["url-decode", "URL 解码", "还原 URL 编码内容", "#/url/decode"],
    ]},
    { id: "text", name: "文本工具", icon: "≡", tools: [
        ["text-diff", "文本对比", "左右对照查看文本差异", "#/text/diff"],
        ["html-render", "HTML 渲染", "在隔离预览区查看 HTML", "#/text/html"],
    ]},
    { id: "crypto", name: "加解密工具", icon: "⌁", tools: [
        ["crypto", "加解密工具", "摘要、HMAC、字符编码和常用对称加密", "#/crypto"],
        ["base64", "Base64", "Base64 编码和解码", "#/crypto/base64"],
        ["hash", "摘要计算", "计算 MD5、SHA-1、SHA-2、SHA-3、SM3 等摘要", "#/crypto/hash"],
    ]},
    { id: "image", name: "图片工具", icon: "▧", tools: [
        ["image-base64", "图片转 Base64", "单张或批量图片转换为 Data URL 或数组", "#/base64/image"],
    ]},
];
let catalogGroups = groups;
const fallbackGroupIcons = Object.fromEntries(groups.map(group => [group.id, group.icon]));
const FAVORITES_KEY = "curl2py.favorite-tools.v1";
let favoriteIds = null;
let draggedFavoriteId = null;

const $ = (selector) => document.querySelector(selector);


const TEXT_MAX_CHARS = 1024 * 1024;
const DIFF_MAX_CHARS = 512 * 1024;

function rejectOversizedText(value, limit, label) {
    if (typeof value !== "string") return null;
    if (value.length <= limit) return null;
    return `${label}过大（前端预检上限约 ${Math.round(limit / 1024)}KB），请缩小后再试`;
}

function bindAutoProcess(inputs, run) {
    let timer;
    const generation = routeGeneration;
    const schedule = () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
            if (generation !== routeGeneration) return;
            run();
        }, 500);
    };
    inputs.filter(Boolean).forEach(input => {
        input.addEventListener("input", schedule);
        input.addEventListener("change", schedule);
    });
    return schedule;
}

function allTools() {
    return catalogGroups.flatMap(group => group.tools.map(tool => ({ tool, group })));
}

function toolById(id) {
    return allTools().find(item => item.tool[0] === id);
}

function readFavorites() {
    if (favoriteIds) return favoriteIds;
    let stored = null;
    try { stored = JSON.parse(localStorage.getItem(FAVORITES_KEY)); } catch (_error) { stored = null; }
    const validIds = new Set(allTools().map(item => item.tool[0]));
    if (Array.isArray(stored)) {
        favoriteIds = [...new Set(stored)].filter(id => validIds.has(id));
    } else {
        // 收藏是用户主动建立的状态，首次使用保持为空，不把“常用”误当收藏。
        favoriteIds = [];
    }
    return favoriteIds;
}

function saveFavorites() {
    try { localStorage.setItem(FAVORITES_KEY, JSON.stringify(favoriteIds || [])); } catch (_error) { /* private mode */ }
}

function isFavorite(id) { return readFavorites().includes(id); }

function toggleFavorite(id) {
    const current = readFavorites();
    if (current.includes(id)) favoriteIds = current.filter(item => item !== id);
    else favoriteIds = [...current, id];
    saveFavorites();
    updateFavoriteUI();
    showToast(isFavorite(id) ? "已加入收藏" : "已取消收藏");
}

function updateFavoriteUI() {
    document.querySelectorAll("[data-favorite-id]").forEach(button => {
        const active = isFavorite(button.dataset.favoriteId);
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
        button.setAttribute("aria-label", `${active ? "取消收藏" : "收藏"}${toolById(button.dataset.favoriteId)?.tool?.[1] || ""}`);
        button.textContent = active ? "★" : "☆";
    });
    // 只刷新侧栏，避免收藏按钮触发整页 route，输入中的工作区状态得以保留。
    if (document.querySelector("#sidebar")) renderSidebar(location.hash || "#/home");
}

window.addEventListener("storage", event => {
    if (event.key !== FAVORITES_KEY) return;
    favoriteIds = null;
    readFavorites();
    updateFavoriteUI();
});

function favoriteTools() {
    return readFavorites().map(id => toolById(id)).filter(Boolean);
}

function displayGroups() {
    return catalogGroups.filter(group => group.id !== "common");
}

function favoriteButton(tool) {
    const active = isFavorite(tool[0]);
    return `<button class="favorite-toggle ${active ? "active" : ""}" type="button" data-favorite-id="${tool[0]}" aria-label="${active ? "取消收藏" : "收藏"}${tool[1]}" aria-pressed="${active}">${active ? "★" : "☆"}</button>`;
}

function bindFavoriteControls() {
    document.querySelectorAll("[data-favorite-id]").forEach(button => {
        button.onclick = event => { event.preventDefault(); event.stopPropagation(); toggleFavorite(button.dataset.favoriteId); };
    });
}

function bindFavoriteDrag() {
    document.querySelectorAll("[data-favorite-item]").forEach(item => {
        item.addEventListener("dragstart", event => { draggedFavoriteId = item.dataset.favoriteItem; item.classList.add("dragging"); event.dataTransfer.effectAllowed = "move"; });
        item.addEventListener("dragover", event => { event.preventDefault(); item.classList.add("drag-over"); });
        item.addEventListener("dragleave", () => item.classList.remove("drag-over"));
        item.addEventListener("drop", event => {
            event.preventDefault();
            const targetId = item.dataset.favoriteItem;
            if (!draggedFavoriteId || draggedFavoriteId === targetId) return;
            const ordered = readFavorites().filter(id => id !== draggedFavoriteId);
            const targetIndex = ordered.indexOf(targetId);
            ordered.splice(targetIndex < 0 ? ordered.length : targetIndex, 0, draggedFavoriteId);
            favoriteIds = ordered; saveFavorites(); route(); showToast("收藏顺序已保存");
        });
        item.addEventListener("dragend", () => { draggedFavoriteId = null; item.classList.remove("dragging"); document.querySelectorAll(".drag-over").forEach(node => node.classList.remove("drag-over")); });
    });
}

function bindSearch() {
    const input = $("#tool-search");
    if (!input || input.dataset.bound) return;
    input.dataset.bound = "true";
    input.addEventListener("input", event => {
        const value = event.target.value.trim().toLowerCase();
        document.querySelectorAll("[data-search]").forEach(item => { item.style.display = item.dataset.search.toLowerCase().includes(value) ? "" : "none"; });
    });
}

function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 1800);
}

function outputText(element) {
    return "value" in element ? element.value : element.textContent;
}

function setOutputText(element, value) {
    if ("value" in element) element.value = value;
    else element.textContent = value;
}

async function copyText(value) {
    if (!value) {
        showToast("暂无可复制内容");
        return;
    }
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(value);
        } else {
            const temporary = document.createElement("textarea");
            temporary.value = value;
            temporary.setAttribute("readonly", "");
            temporary.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
            document.body.appendChild(temporary);
            temporary.select();
            const copied = document.execCommand("copy");
            temporary.remove();
            if (!copied) throw new Error("copy failed");
        }
        showToast("已复制");
    } catch (_error) {
        showToast("复制失败，请手动选择内容复制");
    }
}

async function api(url, data, signal) {
    if (data && typeof data === "object") {
        for (const [key, value] of Object.entries(data)) {
            const limit = (key === "left" || key === "right") ? DIFF_MAX_CHARS : TEXT_MAX_CHARS;
            const oversized = rejectOversizedText(value, limit, key);
            if (oversized) {
                setGuideState("error", oversized);
                return { success: false, error: oversized };
            }
        }
    }
    setGuideState("loading", "正在等待本地服务返回结果。");
    const controller = new AbortController();
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 20000);
    const abortRequest = () => controller.abort();
    if (signal) {
        if (signal.aborted) controller.abort();
        else signal.addEventListener("abort", abortRequest, { once: true });
    }
    try {
        const headers = { "Content-Type": "application/json" };
        const token = window.CURL2PY_API_TOKEN;
        if (token) headers["X-API-Token"] = token;
        const response = await fetch(url, {
            method: "POST",
            headers,
            body: JSON.stringify(data),
            signal: controller.signal,
        });
        const contentType = response.headers.get("content-type") || "";
        const result = contentType.includes("application/json") ? await response.json() : {};
        if (!response.ok) {
            const failure = { success: false, status: response.status, error: result.error || `请求失败（HTTP ${response.status}）` };
            setGuideState("error", failure.error);
            return failure;
        }
        const finalResult = result;
        if (finalResult.success === false) setGuideState("error", finalResult.error || "处理失败");
        else if (finalResult.warnings?.length) setGuideState("warning", `结果已返回，${finalResult.warnings.length} 条警告`);
        else setGuideState("success", "结果已更新，可以继续编辑或复制。");
        return finalResult;
    } catch (_error) {
        if (_error.name === "AbortError") {
            const aborted = timedOut ? { success: false, error: "请求超时，请稍后重试" } : { success: false, aborted: true };
            if (timedOut) setGuideState("error", aborted.error);
            return aborted;
        }
        const unavailable = { success: false, error: "服务暂时不可用，请检查 Docker 容器是否运行" };
        setGuideState("error", unavailable.error);
        return unavailable;
    } finally {
        clearTimeout(timeout);
        if (signal) signal.removeEventListener("abort", abortRequest);
    }
}

async function loadCatalog() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
        const headers = {};
        const token = window.CURL2PY_API_TOKEN;
        if (token) headers["X-API-Token"] = token;
        const response = await fetch("/api/tools", { headers, signal: controller.signal });
        if (!response.ok) return;
        const data = await response.json();
        if (!Array.isArray(data.groups)) return;
        catalogGroups = data.groups.map(group => ({
            ...group,
            icon: group.icon || fallbackGroupIcons[group.id] || "•",
            tools: group.tools.map(tool => [tool.id, tool.name, tool.description, tool.route]).filter(tool => tool[3]),
        }));
        groups = catalogGroups;
        favoriteIds = null;
        readFavorites();
    } catch (_error) {
        // 使用内置目录作为离线/后端异常时的降级方案。
    } finally {
        clearTimeout(timeout);
    }
}

async function runnerHealth() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
        const headers = {};
        if (window.CURL2PY_API_TOKEN) headers["X-API-Token"] = window.CURL2PY_API_TOKEN;
        const response = await fetch("/health", { headers, signal: controller.signal });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) return { success: false, error: response.status === 401 ? "健康检查未授权，请检查 API Token" : `健康检查失败（HTTP ${response.status}）` };
        if (!data.runner_instance) return { success: false, error: "服务未返回 runner 实例，无法确认运行授权" };
        if (typeof data.runner_enabled !== "boolean") return { success: false, error: "服务返回的 runner 状态无效" };
        return { success: true, ...data };
    } catch (error) {
        return { success: false, error: error.name === "AbortError" ? "健康检查超时，请稍后重试" : "无法连接服务，请检查 Docker 容器是否运行" };
    } finally { clearTimeout(timeout); }
}

async function requestPythonAuthorization(run, expectedGeneration = routeGeneration) {
    const health = await runnerHealth();
    if (expectedGeneration !== routeGeneration || location.hash !== "#/curl/python") return;
    if (!health.success) { setGuideState("blocked", health.error); showToast(health.error); return; }
    if (!health.runner_enabled) { setGuideState("blocked", "脚本运行未就绪：请设置 ENABLE_SCRIPT_RUNNER=true 并配置 CURL2PY_API_TOKEN"); showToast("脚本运行未就绪：请设置 ENABLE_SCRIPT_RUNNER=true 并配置 CURL2PY_API_TOKEN"); return; }
    // 授权绑定 Docker runner 实例：刷新或重新打开页面不重复弹窗，容器重启后自动失效。
    const sessionKey = "curl2py.python-run-authorized.v1";
    try {
        const saved = JSON.parse(localStorage.getItem(sessionKey) || "null");
        if (saved?.authorized === true && saved.runnerInstance === health.runner_instance) { run(); return; }
    } catch (_error) { /* storage unavailable; require confirmation */ }
    const modal = $("#python-risk-modal");
    const confirm = $("#python-risk-confirm");
    const cancel = $("#python-risk-cancel");
    if (!modal || !confirm || !cancel) { showToast("无法打开风险确认弹窗"); return; }
    const runner = $("#run-python");
    const previousFocus = document.activeElement;
    const focusable = () => [...modal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")].filter(node => !node.disabled);
    const close = () => { modal.hidden = true; (runner || previousFocus)?.focus?.(); document.removeEventListener("keydown", onKeydown); };
    const onKeydown = event => {
        if (event.key === "Escape") { event.preventDefault(); close(); return; }
        if (event.key !== "Tab") return;
        const nodes = focusable(); if (!nodes.length) return;
        const first = nodes[0]; const last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    modal.hidden = false;
    modal.__riskKeydown = onKeydown;
    document.addEventListener("keydown", onKeydown);
    cancel.onclick = close;
    modal.querySelector("[data-risk-cancel]").onclick = close;
    confirm.onclick = () => {
        try { localStorage.setItem(sessionKey, JSON.stringify({ authorized: true, runnerInstance: health.runner_instance })); } catch (_error) { /* confirmation remains one-time */ }
        close();
        run();
    };
    confirm.focus();
}

function setGuideState(state, detail = "") {
    const guide = $("#workflow-guide");
    if (!guide) return;
    const labels = {
        idle: ["准备就绪", "输入内容后，工具会在本地处理。", "canonical"],
        loading: ["处理中", "正在等待本地服务返回结果。", "action"],
        success: ["已完成", "结果已更新，可以继续编辑或复制。", "canonical"],
        warning: ["需要检查", "结果已返回，请先查看提示再继续。", "canonical"],
        error: ["处理失败", "请检查输入或重试；原始内容仍保留在编辑器中。", "canonical"],
        blocked: ["操作受限", "当前环境或权限不允许继续执行。", "canonical"],
        recovery: ["可恢复", "可以清空输入、修正内容后重新处理。", "canonical"],
    };
    const [title, fallback, imageMode] = labels[state] || labels.idle;
    const copy = detail || fallback;
    guide.dataset.state = state;
    guide.setAttribute("aria-label", `${title}：${copy}`);
    const stateTitle = guide.querySelector("[data-guide-title]");
    const stateDetail = guide.querySelector("[data-guide-detail]");
    const image = guide.querySelector("[data-guide-image]");
    if (stateTitle) stateTitle.textContent = title;
    if (stateDetail) stateDetail.textContent = copy;
    if (image) {
        image.src = imageMode === "action"
            ? "/static/fantasy-mouse/visual-grounding/processing-action-hands-sm.png"
            : "/static/fantasy-mouse/visual-grounding/canonical-protagonist-sm.png";
        image.alt = imageMode === "action" ? "Fantasy Mouse 以动作手势表示正在处理" : `Fantasy Mouse 工作向导：${title}`;
    }
    const live = guide.querySelector("[data-guide-live]");
    if (live) live.textContent = `${title}。${copy}`;
}

function workflowGuide(compact = false) {
    return `<aside class="workflow-guide${compact ? " compact" : ""}" id="workflow-guide" data-state="idle" aria-label="准备就绪：输入内容后，工具会在本地处理。">
        <img data-guide-image src="/static/fantasy-mouse/visual-grounding/canonical-protagonist-sm.png" alt="Fantasy Mouse 工作向导：准备就绪" width="92" height="76" decoding="async" fetchpriority="low">
        <div class="workflow-guide-copy"><span class="guide-kicker">工作台向导</span><strong data-guide-title>准备就绪</strong><span data-guide-detail>输入内容后，工具会在本地处理。</span><span class="sr-only" data-guide-live role="status" aria-live="polite" aria-atomic="true">准备就绪。输入内容后，工具会在本地处理。</span></div>
    </aside>`;
}

function setStatus(element, state, text) {
    if (!element) return;
    element.className = `status ${state === "success" ? "success" : state === "warning" ? "warning" : state === "error" || state === "blocked" ? "error" : ""}`;
    element.setAttribute("role", "status");
    element.setAttribute("aria-live", "polite");
    element.setAttribute("aria-atomic", "true");
    element.textContent = text || "";
    setGuideState(state, text);
}

function setToolWarning(messages) {
    let warning = $("#tool-warning");
    if (!warning) {
        warning = document.createElement("div");
        warning.id = "tool-warning";
        warning.className = "warning-box";
        $("#tool-output").parentElement.appendChild(warning);
    }
    warning.hidden = !messages.length;
    warning.textContent = messages.join("\n");
}

function renderSidebar(activeHash) {
    const favorites = favoriteTools();
    const favoriteMarkup = `<div class="side-group favorite-group"><div class="side-heading">常用工具</div>${favorites.length ? favorites.map(({ tool }) => `<div class="favorite-item" data-favorite-item="${tool[0]}" data-search="${tool[1]} ${tool[2]}" draggable="true"><a class="side-item ${tool[3] === activeHash ? "active" : ""}" href="${tool[3]}"><span class="side-icon">★</span><span>${tool[1]}</span></a>${favoriteButton(tool)}</div>`).join("") : '<div class="favorite-empty">尚未收藏工具<br><small>点击星标添加</small></div>'}</div>`;
    const groupsMarkup = displayGroups().map(group => `
        <div class="side-group">
            <div class="side-heading">${group.name}</div>
            ${group.tools.map(tool => `<div class="side-tool"><a class="side-item ${tool[3] === activeHash ? "active" : ""}" href="${tool[3]}"><span class="side-icon">${group.icon}</span><span>${tool[1]}</span></a>${favoriteButton(tool)}</div>`).join("")}
        </div>
    `).join("");
    $("#sidebar").innerHTML = favoriteMarkup + groupsMarkup;
    bindFavoriteControls();
    bindFavoriteDrag();
}

function renderHome() {
    document.querySelector(".app-body")?.classList.remove("focus-workspace");
    $("#view").className = "main-content";
    renderSidebar("#/home");
    const favorites = favoriteTools();
    $("#view").innerHTML = `
        <section class="hero">
            <div><p class="eyebrow">LOCAL DEVELOPER TOOLBOX</p><h1>爬虫开发工具库</h1><p>面向爬虫开发者的本地化工具集合，集中处理请求转换、数据格式化、文本对比和时间转换。</p><div class="hero-actions"><a class="btn primary" href="#/curl/python">进入请求转换</a></div></div>
            <div class="hero-guide">${workflowGuide()}<div class="hero-note">本地运行 · Docker 部署 · 数据不外传<br><small>输入或选项变化后自动处理，避免重复点击。</small></div></div>
        </section>
        <h2 class="group-title">常用工具</h2><div class="tool-grid favorite-grid">${favorites.length ? favorites.map(({ tool }) => card(tool)).join("") : '<div class="empty">尚未收藏工具。点击工具卡片右上角的星标添加常用工具。</div>'}</div>
        ${displayGroups().map(group => `<h2 class="group-title">${group.name}</h2><div class="tool-grid">${group.tools.map(card).join("")}</div>`).join("")}
    `;
    bindFavoriteControls();
    bindSearch();
}

function card(tool) {
    return `<article class="tool-card" data-search="${tool[1]} ${tool[2]}"><span class="tool-card-icon" aria-hidden="true">${tool[1].slice(0, 1)}</span>${favoriteButton(tool)}<h3>${tool[1]}</h3><p>${tool[2]}</p><a class="card-link" href="${tool[3]}">进入工具<span aria-hidden="true"> →</span></a></article>`;
}

function workHeader(title, description, category) {
    const currentTool = toolById({
        "#/curl/python": "curl-python", "#/curl/feapder": "curl-feapder",
        "#/json/diff": "json-diff", "#/text/diff": "text-diff", "#/text/html": "html-render",
        "#/time": "timestamp", "#/crypto": "crypto", "#/crypto/base64": "base64",
        "#/crypto/hash": "hash", "#/base64/image": "image-base64", "#/vault": "cookie-vault",
    }[location.hash]) || allTools().find(item => item.tool[3] === location.hash);
    return `<div class="breadcrumb">首页 / ${category} / ${title}</div><div class="work-header"><div><h1>${title}</h1>${description ? `<p>${description}</p>` : ""}</div>${currentTool ? favoriteButton(currentTool.tool) : ""}</div>`;
}

function workShell(title, description, category, content, options = {}) {
    renderSidebar(location.hash);
    const focus = options.compact !== false;
    document.querySelector(".app-body")?.classList.toggle("focus-workspace", focus);
    const view = $("#view");
    view.className = `main-content${focus ? " compact-tool" : ""}`;
    view.innerHTML = workHeader(title, description, category) + workflowGuide(true) + content;
    bindFavoriteControls();
}

function renderCurl(mode) {
    const feapder = mode === "feapder";
    const output = feapder
        ? '<pre class="output" id="tool-output" aria-label="生成的 Feapder 代码"></pre>'
        : '<textarea class="editor output-editor" id="tool-output" spellcheck="false" aria-label="生成的 Python 代码" placeholder="转换后的 Python 代码可在这里编辑"></textarea>';
    const terminal = feapder ? "" : '<section class="panel terminal-panel"><div class="panel-head">运行终端<button id="terminal-clear" class="link-button" type="button">清空终端</button></div><div class="panel-body"><pre class="output terminal-output" id="terminal-output" role="log" aria-live="polite">等待运行 Python 脚本…</pre></div></section>';
    workShell(feapder ? "curl 转 Feapder" : "curl 转 Python requests", feapder ? "先解析请求结构，再生成 Feapder 爬虫模板。" : "将浏览器复制的 curl 命令转换为可运行的 Python 请求代码。", "请求转换", `
        <div class="toolbar">${feapder ? "" : '<button class="btn" id="run-python" type="button">运行 Python</button><button class="btn" id="copy-cookie" type="button">复制 Cookie</button><button class="btn" id="save-cookie" type="button">保存 Cookie</button>'}<button class="btn" id="load-example" type="button">加载示例</button><button class="btn" id="clear-tool" type="button">清空</button></div>
        <div class="work-grid"><section class="panel"><div class="panel-head">输入 curl 命令<span>支持多行</span></div><div class="panel-body"><textarea class="editor" id="tool-input" aria-label="输入 curl 命令" placeholder="curl 'https://example.com/api?page=1' -H 'Accept: application/json'"></textarea></div></section><section class="panel"><div class="panel-head">生成代码<button class="link-button" id="copy-output" type="button">复制结果</button></div><div class="panel-body">${output}<div class="warning-box" id="tool-warning" hidden role="alert"></div><div class="status" id="tool-status" role="status" aria-live="polite" aria-atomic="true"></div></div></section></div>${terminal}
    `);
    let convertTimer;
    let convertRequestId = 0;
    const convertCurl = async () => {
        const curl = $("#tool-input").value;
        if (!curl.trim()) {
            setOutputText($("#tool-output"), "");
            setStatus($("#tool-status"), "idle", "等待输入 curl 命令");
            setToolWarning([]);
            return;
        }
        const requestId = ++convertRequestId;
        const status = $("#tool-status");
        status.dataset.requestId = String(requestId);
        status.className = "status";
        status.textContent = "正在转换…";
        setGuideState("loading", "正在解析输入并生成代码。");
        const data = await api(feapder ? "/api/convert/curl2feapder" : "/api/convert/curl2python", { curl });
        if (requestId !== convertRequestId) return;
        setOutputText($("#tool-output"), data.success ? data.result : data.error);
        status.className = `status ${data.success ? (data.warnings?.length ? "warning" : "success") : "error"}`;
        status.textContent = data.success ? (data.warnings?.length ? `转换完成，${data.warnings.length} 条警告` : "转换成功") : "转换失败：请检查输入命令";
        setGuideState(data.success ? (data.warnings?.length ? "warning" : "success") : "error", status.textContent);
        setToolWarning(data.success ? (data.warnings || []) : []);
    };
    const scheduleConvert = () => {
        clearTimeout(convertTimer);
        
        convertTimer = setTimeout(convertCurl, 600);
    };
    $("#tool-input").addEventListener("input", scheduleConvert);
    $("#load-example").onclick = () => {
        $("#tool-input").value = "curl 'https://example.com/api?page=1' -H 'Accept: application/json'";
        scheduleConvert();
    };
    $("#clear-tool").onclick = () => {
        clearTimeout(convertTimer);
        convertRequestId += 1;
        $("#tool-input").value = "";
        setOutputText($("#tool-output"), "");
        setStatus($("#tool-status"), "recovery", "已清空，可以重新输入");
        setToolWarning([]);
        if ($("#terminal-output")) { $("#terminal-output").textContent = "等待运行 Python 脚本…"; setGuideState("recovery", "已清空，可以重新输入。"); }
    };
    $("#copy-output").onclick = () => copyText(outputText($("#tool-output")));
    if (!feapder) {
        $("#copy-cookie").onclick = () => copyCookieFromCurl();
        $("#save-cookie").onclick = () => saveCookieFromCurl();
    }
    if (!feapder) {
        $("#run-python").onclick = () => requestPythonAuthorization(async () => {
            const code = outputText($("#tool-output"));
            const terminalOutput = $("#terminal-output");
            const runButton = $("#run-python");
            if (!code.trim()) { terminalOutput.textContent = "请先转换 curl，或在右侧代码框中输入 Python 脚本。"; setGuideState("recovery", "请先生成或编辑 Python 代码。"); return; }
            runButton.disabled = true;
            runButton.textContent = "运行中…";
            terminalOutput.textContent = "正在运行…";
            setGuideState("loading", "正在执行已授权的 Python 脚本。");
            try {
                const data = await api("/api/run/python", { code });
                if (!data.success) {
                    terminalOutput.textContent = data.status === 403
                        ? "运行失败：脚本运行未就绪。请设置 ENABLE_SCRIPT_RUNNER=true、配置 CURL2PY_API_TOKEN 后重建/重启容器。"
                        : `运行失败：${data.error}`;
                    setGuideState("blocked", terminalOutput.textContent);
                    return;
                }
                const result = data.result;
                const header = result.timed_out ? "脚本执行超时（12 秒）" : `进程退出码：${result.returncode}`;
                terminalOutput.textContent = [header, result.stdout, result.stderr ? `错误输出：\n${result.stderr}` : ""].filter(Boolean).join("\n");
                setGuideState(result.timed_out || result.returncode !== 0 ? "error" : "success", header);
            } finally {
                runButton.disabled = false;
                runButton.textContent = "运行 Python";
            }
        });
        $("#terminal-clear").onclick = () => { $("#terminal-output").textContent = "等待运行 Python 脚本…"; };
    }
    if (!feapder) {
        // 进入工作页即做一次健康检查；requestPythonAuthorization 按 runner_instance 去重。
        const generation = routeGeneration;
        requestPythonAuthorization(() => {}, generation);
    }
}

/* ===== Cookie 收藏夹 =====
   票据只放在页面内存里：刷新即丢失。丢失后由 vaultAttach() 用请求签名
   自动换一张新票（服务端仍处于解锁态时无需再输密码），避免出现
   「明明已解锁、一操作就提示已锁定」的假状态。 */
let vaultToken = "";
let vaultTags = [];
let vaultActiveTag = "";

// 收藏夹请求签名：sign = MD5("GGB" + 时间戳)，后端校验时间戳在 5 分钟窗口内。
// SECRET 与后端 cookie_vault.DEFAULT_SIGN_SECRET（默认 "GGB"，可用环境变量
// VAULT_SIGN_SECRET 覆盖）必须一致——改动时两处要同步。

// 紧凑 MD5（RFC 1321 公共实现改写），仅用于上述签名计算。
function md5cycle(x, k) {
    let a = x[0], b = x[1], c = x[2], d = x[3];
    a = ff(a, b, c, d, k[0], 7, -680876936);
    d = ff(d, a, b, c, k[1], 12, -389564586);
    c = ff(c, d, a, b, k[2], 17, 606105819);
    b = ff(b, c, d, a, k[3], 22, -1044525330);
    a = ff(a, b, c, d, k[4], 7, -176418897);
    d = ff(d, a, b, c, k[5], 12, 1200080426);
    c = ff(c, d, a, b, k[6], 17, -1473231341);
    b = ff(b, c, d, a, k[7], 22, -45705983);
    a = ff(a, b, c, d, k[8], 7, 1770035416);
    d = ff(d, a, b, c, k[9], 12, -1958414417);
    c = ff(c, d, a, b, k[10], 17, -42063);
    b = ff(b, c, d, a, k[11], 22, -1990404162);
    a = ff(a, b, c, d, k[12], 7, 1804603682);
    d = ff(d, a, b, c, k[13], 12, -40341101);
    c = ff(c, d, a, b, k[14], 17, -1502002290);
    b = ff(b, c, d, a, k[15], 22, 1236535329);
    a = gg(a, b, c, d, k[1], 5, -165796510);
    d = gg(d, a, b, c, k[6], 9, -1069501632);
    c = gg(c, d, a, b, k[11], 14, 643717713);
    b = gg(b, c, d, a, k[0], 20, -373897302);
    a = gg(a, b, c, d, k[5], 5, -701558691);
    d = gg(d, a, b, c, k[10], 9, 38016083);
    c = gg(c, d, a, b, k[15], 14, -660478335);
    b = gg(b, c, d, a, k[4], 20, -405537848);
    a = gg(a, b, c, d, k[9], 5, 568446438);
    d = gg(d, a, b, c, k[14], 9, -1019803690);
    c = gg(c, d, a, b, k[3], 14, -187363961);
    b = gg(b, c, d, a, k[8], 20, 1163531501);
    a = gg(a, b, c, d, k[13], 5, -1444681467);
    d = gg(d, a, b, c, k[2], 9, -51403784);
    c = gg(c, d, a, b, k[7], 14, 1735328473);
    b = gg(b, c, d, a, k[12], 20, -1926607734);
    a = hh(a, b, c, d, k[5], 4, -378558);
    d = hh(d, a, b, c, k[8], 11, -2022574463);
    c = hh(c, d, a, b, k[11], 16, 1839030562);
    b = hh(b, c, d, a, k[14], 23, -35309556);
    a = hh(a, b, c, d, k[1], 4, -1530992060);
    d = hh(d, a, b, c, k[4], 11, 1272893353);
    c = hh(c, d, a, b, k[7], 16, -155497632);
    b = hh(b, c, d, a, k[10], 23, -1094730640);
    a = hh(a, b, c, d, k[13], 4, 681279174);
    d = hh(d, a, b, c, k[0], 11, -358537222);
    c = hh(c, d, a, b, k[3], 16, -722521979);
    b = hh(b, c, d, a, k[6], 23, 76029189);
    a = hh(a, b, c, d, k[9], 4, -640364487);
    d = hh(d, a, b, c, k[12], 11, -421815835);
    c = hh(c, d, a, b, k[15], 16, 530742520);
    b = hh(b, c, d, a, k[2], 23, -995338651);
    a = ii(a, b, c, d, k[0], 6, -198630844);
    d = ii(d, a, b, c, k[7], 10, 1126891415);
    c = ii(c, d, a, b, k[14], 15, -1416354905);
    b = ii(b, c, d, a, k[5], 21, -57434055);
    a = ii(a, b, c, d, k[12], 6, 1700485571);
    d = ii(d, a, b, c, k[3], 10, -1894986606);
    c = ii(c, d, a, b, k[10], 15, -1051523);
    b = ii(b, c, d, a, k[1], 21, -2054922799);
    a = ii(a, b, c, d, k[8], 6, 1873313359);
    d = ii(d, a, b, c, k[15], 10, -30611744);
    c = ii(c, d, a, b, k[6], 15, -1560198380);
    b = ii(b, c, d, a, k[13], 21, 1309151649);
    a = ii(a, b, c, d, k[4], 6, -145523070);
    d = ii(d, a, b, c, k[11], 10, -1120210379);
    c = ii(c, d, a, b, k[2], 15, 718787259);
    b = ii(b, c, d, a, k[9], 21, -343485551);
    x[0] = add32(a, x[0]);
    x[1] = add32(b, x[1]);
    x[2] = add32(c, x[2]);
    x[3] = add32(d, x[3]);
}
function cmn(q, a, b, x, s, t) {
    a = add32(add32(a, q), add32(x, t));
    return add32((a << s) | (a >>> (32 - s)), b);
}
function ff(a, b, c, d, x, s, t) { return cmn((b & c) | (~b & d), a, b, x, s, t); }
function gg(a, b, c, d, x, s, t) { return cmn((b & d) | (c & ~d), a, b, x, s, t); }
function hh(a, b, c, d, x, s, t) { return cmn(b ^ c ^ d, a, b, x, s, t); }
function ii(a, b, c, d, x, s, t) { return cmn(c ^ (b | ~d), a, b, x, s, t); }
function md51(s) {
    const n = s.length;
    const state = [1732584193, -271733879, -1732584194, 271733878];
    let i;
    for (i = 64; i <= n; i += 64) md5cycle(state, md5blk(s.substring(i - 64, i)));
    s = s.substring(i - 64);
    const tail = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    for (i = 0; i < s.length; i++) tail[i >> 2] |= s.charCodeAt(i) << ((i % 4) * 8);
    tail[i >> 2] |= 0x80 << ((i % 4) * 8);
    if (i > 55) {
        md5cycle(state, tail);
        for (i = 0; i < 16; i++) tail[i] = 0;
    }
    tail[14] = n * 8;
    md5cycle(state, tail);
    return state;
}
function md5blk(s) {
    const blks = [];
    for (let i = 0; i < 64; i += 4)
        blks[i >> 2] = s.charCodeAt(i) + (s.charCodeAt(i + 1) << 8) + (s.charCodeAt(i + 2) << 16) + (s.charCodeAt(i + 3) << 24);
    return blks;
}
const MD5_HEX = "0123456789abcdef".split("");
function rhex(n) {
    let s = "";
    for (let j = 0; j < 4; j++) s += MD5_HEX[(n >> (j * 8 + 4)) & 0x0f] + MD5_HEX[(n >> (j * 8)) & 0x0f];
    return s;
}
function add32(a, b) { return (a + b) & 0xffffffff; }
function md5(str) {
    // 先转成 UTF-8 字节串，保证中文/emoji 与后端 hashlib.md5 结果一致。
    return md51(unescape(encodeURIComponent(str))).map(rhex).join("");
}

const VAULT_SIGN_SECRET = "GGB";

function vaultSignHeaders() {
    const ts = String(Math.floor(Date.now() / 1000));
    return { "X-Vault-Ts": ts, "X-Vault-Sign": md5(VAULT_SIGN_SECRET + ts) };
}

// 页面刷新后票据丢失：只要服务端还处于解锁态，用有效签名换一张新票据。
async function vaultAttach() {
    const data = await vaultApi("/api/vault/attach", {}, false);
    if (data.success && data.token) { vaultToken = data.token; return true; }
    vaultToken = "";
    return false;
}


async function vaultApi(path, body = {}, retry = true) {
    const headers = { "Content-Type": "application/json", ...vaultSignHeaders() };
    if (window.CURL2PY_API_TOKEN) headers["X-API-Token"] = window.CURL2PY_API_TOKEN;
    if (vaultToken) headers["X-Vault-Token"] = vaultToken;
    try {
        const response = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401 && !path.endsWith("/unlock") && !path.endsWith("/init")) vaultToken = "";
            // 票据过期（5 分钟）但服务端可能仍在解锁态：用签名自动换票并重试一次。
            if (response.status === 401 && retry && !path.endsWith("/unlock") && !path.endsWith("/init") && !path.endsWith("/attach")) {
                if (await vaultAttach()) return vaultApi(path, body, false);
            }
            return { success: false, status: response.status, error: data.error || `请求失败（HTTP ${response.status}）` };
        }
        return data;
    } catch (_error) {
        return { success: false, error: "服务暂时不可用，请检查 Docker 容器是否运行" };
    }
}

// 主密码不设任何强度限制：位数不限、纯数字可以、纯符号也可以，全部由用户决定。
// 这里只做位数计数与风险提示，不拦提交——唯一的硬性要求是"不能为空"，
// 因为空口令等于没有口令。真正抬高爆破成本的是 12 万次 PBKDF2，不是口令规则。
function vaultPasswordHint(input, hint) {
    if (!input || !hint) return;
    const update = () => {
        const password = input.value;
        hint.classList.remove("ok", "bad");
        hint.textContent = password
            ? `${password.length} 位（不限长度与字符组合）`
            : "不限长度与字符组合，纯数字也可以；忘记后无法找回";
    };
    input.addEventListener("input", update);
    update();
}

function vaultTagChips(tags, active) {
    return `<button class="vault-tag-chip ${active === "" ? "active" : ""}" type="button" data-vault-tag="">全部</button>` +
        tags.map(item => `<button class="vault-tag-chip ${active === item.tag ? "active" : ""}" type="button" data-vault-tag="${item.tag}">${item.tag}<span>${item.count}</span></button>`).join("");
}

function vaultGateMarkup(state) {
    if (!state.initialized) {
        return `<section class="vault-gate">
            <h2>首次使用：设置主密码</h2>
            <p class="muted-text">主密码只用于加解密收藏内容，服务端不保存密码本身。<strong>忘记后无法找回</strong>——数据库里只有密文。</p>
            <div class="field"><label for="vault-pass">主密码</label><input type="password" id="vault-pass" autocomplete="new-password" placeholder="不限长度与字符组合"></div>
            <p class="vault-pass-hint" id="vault-pass-hint" aria-live="polite"></p>
            <div class="field"><label for="vault-pass2">再输入一次</label><input type="password" id="vault-pass2" autocomplete="new-password"></div>
            <div class="vault-gate-actions"><button class="btn primary" id="vault-setup" type="button">创建并解锁</button></div>
            <div class="status" id="vault-gate-status" role="status" aria-live="polite"></div>
            <p class="muted-text">加密方案：PBKDF2-HMAC-SHA256（${state.iterations || 120000} 次迭代）派生密钥 → AES-256-GCM 逐条加密；数据密钥再由该密钥封装后存库。</p>
        </section>`;
    }
    return `<section class="vault-gate">
        <h2>收藏夹已锁定</h2>
        <p class="muted-text">已保存 ${state.count} 条记录。输入主密码解锁，票据 5 分钟有效、使用期间自动续期；页面刷新后用请求签名自动换新票。</p>
        <div class="field"><label for="vault-pass">主密码</label><input type="password" id="vault-pass" autocomplete="current-password"></div>
        <p class="vault-pass-hint" id="vault-pass-hint" aria-live="polite"></p>
        <div class="vault-gate-actions"><button class="btn primary" id="vault-unlock" type="button">解锁</button></div>
        <div class="status" id="vault-gate-status" role="status" aria-live="polite">${state.locked_out ? (state.lock_message || "") : ""}</div>
    </section>`;
}

function vaultListMarkup(items) {
    if (!items.length) {
        return `<div class="empty">${vaultActiveTag ? `标签「${vaultActiveTag}」下还没有 Cookie` : "收藏夹是空的。点击上面的「新增 Cookie」，或在 curl 转 Python 页用「保存 Cookie」一键存入。"}</div>`;
    }
    return `<div class="vault-list">${items.map(item => `
        <article class="vault-item" data-vault-id="${item.id}">
            <div class="vault-item-main">
                <div class="vault-title"><strong>${item.name}</strong>${item.domain ? `<span class="vault-domain">${item.domain}</span>` : ""}</div>
                <div class="vault-tags">${item.tags.length ? item.tags.map(tag => `<span class="vault-tag">${tag}</span>`).join("") : '<span class="vault-tag muted">无标签</span>'}</div>
                <code class="vault-value" data-vault-value hidden>••••••••••••</code>
                <div class="vault-meta">更新于 ${new Date(item.updated_at * 1000).toLocaleString("zh-CN", { hour12: false })}</div>
            </div>
            <div class="vault-actions">
                <button class="btn" type="button" data-vault-act="reveal">显示</button>
                <button class="btn" type="button" data-vault-act="copy">复制</button>
                <button class="btn" type="button" data-vault-act="edit">编辑</button>
                <button class="btn danger" type="button" data-vault-act="delete">删除</button>
            </div>
        </article>`).join("")}</div>`;
}

async function renderVault() {
    workShell("Cookie 收藏夹", "Cookie 加密保存在本机 SQLite，主密码不落盘；MCP 既读不到也写不进。", "Cookie 管理",
        `<div id="vault-root"><p class="muted-text">正在读取收藏夹状态…</p></div>`);
    const root = $("#vault-root");
    const state = await vaultApi("/api/vault/status");
    if (!state.success) { root.innerHTML = `<div class="empty">${state.error}</div>`; return; }
    if (state.error) { root.innerHTML = `<div class="empty">${state.error}</div>`; return; }
    if (!state.enabled) {
        root.innerHTML = `<div class="empty">收藏夹未启用（容器环境变量 <code>VAULT_ENABLED=0</code>）。</div>`;
        return;
    }
    if (!state.initialized || !state.unlocked) {
        root.innerHTML = vaultGateMarkup(state);
        vaultPasswordHint($("#vault-pass"), $("#vault-pass-hint"));
        const submit = async () => {
            const password = $("#vault-pass").value;
            const status = $("#vault-gate-status");
            if (!state.initialized) {
                if (password !== $("#vault-pass2").value) { status.className = "status error"; status.textContent = "两次输入的密码不一致"; return; }
            }
            if (!password) { status.className = "status error"; status.textContent = "请输入主密码"; return; }
            status.className = "status"; status.textContent = "正在处理…";
            const data = await vaultApi(state.initialized ? "/api/vault/unlock" : "/api/vault/init", { password });
            if (!data.success) { status.className = "status error"; status.textContent = data.error; return; }
            vaultToken = data.token;
            showToast(state.initialized ? "已解锁" : "主密码已设置");
            renderVault();
        };
        $("#vault-setup")?.addEventListener("click", submit);
        $("#vault-unlock")?.addEventListener("click", submit);
        $("#vault-pass")?.addEventListener("keydown", event => { if (event.key === "Enter") submit(); });
        $("#vault-pass2")?.addEventListener("keydown", event => { if (event.key === "Enter") submit(); });
        return;
    }
    // 服务端已解锁，但本页票据可能因刷新丢失——先用签名换票，失败才回锁定页。
    if (!vaultToken && !(await vaultAttach())) {
        root.innerHTML = vaultGateMarkup({ ...state, initialized: true });
        vaultPasswordHint($("#vault-pass"), $("#vault-pass-hint"));
        const gateStatus = $("#vault-gate-status");
        const submit = async () => {
            const password = $("#vault-pass").value;
            if (!password) { gateStatus.className = "status error"; gateStatus.textContent = "请输入主密码"; return; }
            gateStatus.className = "status"; gateStatus.textContent = "正在处理…";
            const data = await vaultApi("/api/vault/unlock", { password });
            if (!data.success) { gateStatus.className = "status error"; gateStatus.textContent = data.error; return; }
            vaultToken = data.token;
            showToast("已解锁");
            renderVault();
        };
        $("#vault-unlock")?.addEventListener("click", submit);
        $("#vault-pass")?.addEventListener("keydown", event => { if (event.key === "Enter") submit(); });
        return;
    }
    root.innerHTML = `
        <div class="toolbar">
            <button class="btn primary" id="vault-add" type="button">＋ 新增 Cookie</button>
            <input class="vault-search" id="vault-search" placeholder="搜索名称、域名或标签" autocomplete="off">
            <button class="btn" id="vault-lock" type="button">锁定</button>
        </div>
        <div class="vault-tagbar" id="vault-tagbar"></div>
        <div id="vault-list"></div>`;
    const refresh = async () => {
        const query = $("#vault-search")?.value.trim() || "";
        const [list, tags] = await Promise.all([
            vaultApi("/api/vault/list", { query, tag: vaultActiveTag }),
            vaultApi("/api/vault/tags"),
        ]);
        if (!list.success) {
            $("#vault-list").innerHTML = `<div class="empty">${list.error}${list.status === 401 ? "（<a href=\"#/vault\">重新解锁</a>）" : ""}</div>`;
            return;
        }
        vaultTags = tags.success ? tags.result : [];
        $("#vault-tagbar").innerHTML = vaultTagChips(vaultTags, vaultActiveTag);
        $("#vault-list").innerHTML = vaultListMarkup(list.result);
        $("#vault-tagbar").querySelectorAll("[data-vault-tag]").forEach(chip => {
            chip.onclick = () => { vaultActiveTag = chip.dataset.vaultTag; refresh(); };
        });
        $("#vault-list").querySelectorAll("[data-vault-id]").forEach(node => {
            node.querySelectorAll("[data-vault-act]").forEach(button => {
                button.onclick = () => handleVaultAction(button.dataset.vaultAct, node.dataset.vaultId, node);
            });
        });
    };
    let searchTimer;
    $("#vault-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(refresh, 260); });
    $("#vault-add").onclick = () => openCookieDialog({ onSaved: refresh });
    $("#vault-lock").onclick = async () => {
        await vaultApi("/api/vault/lock");
        vaultToken = "";
        showToast("已锁定");
        renderVault();
    };
    refresh();
}

async function handleVaultAction(action, id, node) {
    if (action === "delete") {
        if (!confirm("确定删除这条 Cookie？删除后不可恢复。")) return;
        const data = await vaultApi("/api/vault/delete", { id });
        if (!data.success) { showToast(data.error); return; }
        showToast("已删除");
        node.remove();
        if (!document.querySelector("[data-vault-id]")) renderVault();
        return;
    }
    const data = await vaultApi("/api/vault/detail", { id, with_value: true });
    if (!data.success) { showToast(data.error); return; }
    const item = data.result;
    if (action === "copy") { copyText(item.value); return; }
    if (action === "reveal") {
        const code = node.querySelector("[data-vault-value]");
        const hidden = code.hidden;
        code.hidden = !hidden;
        code.textContent = hidden ? item.value : "••••••••••••";
        node.querySelector('[data-vault-act="reveal"]').textContent = hidden ? "隐藏" : "显示";
        return;
    }
    if (action === "edit") openCookieDialog({ cookie: item, onSaved: renderVault });
}

function cookieTagEditor(initialTags) {
    const selected = [...initialTags];
    const render = () => {
        $("#cookie-tags").innerHTML = selected.length
            ? selected.map(tag => `<span class="tag-chip">${tag}<button type="button" data-remove-tag="${tag}" aria-label="移除标签 ${tag}">×</button></span>`).join("")
            : '<span class="muted-text">还没有标签</span>';
        $("#cookie-tags").querySelectorAll("[data-remove-tag]").forEach(button => {
            button.onclick = () => { const index = selected.indexOf(button.dataset.removeTag); if (index >= 0) selected.splice(index, 1); render(); };
        });
        const options = vaultTags.map(item => item.tag).filter(tag => !selected.includes(tag));
        $("#cookie-tag-options").innerHTML = options.map(tag => `<option value="${tag}"></option>`).join("");
        $("#cookie-tag-suggest").innerHTML = options.length
            ? options.slice(0, 8).map(tag => `<button type="button" class="tag-suggest-btn" data-pick-tag="${tag}">＋ ${tag}</button>`).join("")
            : "";
        $("#cookie-tag-suggest").querySelectorAll("[data-pick-tag]").forEach(button => {
            button.onclick = () => { addTags(button.dataset.pickTag); };
        });
    };
    const addTags = raw => {
        String(raw || "").split(/[,，;；\n]/).map(part => part.trim()).filter(Boolean).forEach(tag => {
            if (selected.length < 12 && !selected.includes(tag)) selected.push(tag.slice(0, 24));
        });
        render();
    };
    render();
    $("#cookie-tag-input").addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== ",") return;
        event.preventDefault();
        addTags(event.target.value);
        event.target.value = "";
    });
    $("#cookie-tag-input").addEventListener("blur", event => { if (event.target.value.trim()) { addTags(event.target.value); event.target.value = ""; } });
    return { get: () => [...selected], add: addTags, render };
}

function openCookieDialog(options = {}) {
    const editing = Boolean(options.cookie?.id);
    const modal = document.createElement("div");
    modal.className = "risk-modal";
    modal.innerHTML = `
        <div class="risk-backdrop" data-close></div>
        <section class="risk-dialog vault-dialog" role="dialog" aria-modal="true" aria-labelledby="cookie-dialog-title">
            <h2 id="cookie-dialog-title">${editing ? "编辑 Cookie" : "保存 Cookie"}</h2>
            <div class="field"><label for="cookie-name">名称</label><input id="cookie-name" maxlength="80" placeholder="例如：知乎 登录态"></div>
            <div class="field"><label for="cookie-domain">域名</label><input id="cookie-domain" maxlength="120" placeholder="例如：www.zhihu.com"></div>
            <div class="field"><label for="cookie-tag-input">标签<span class="muted-text">（回车或逗号添加，便于以后筛选）</span></label>
                <div class="tag-picker" id="cookie-tags"></div>
                <div class="tag-input-row"><input id="cookie-tag-input" list="cookie-tag-options" placeholder="输入标签后回车，例如：知乎、登录态、待更新"><datalist id="cookie-tag-options"></datalist></div>
                <div class="tag-suggest" id="cookie-tag-suggest"></div>
            </div>
            <div class="field"><label for="cookie-value">Cookie 内容<span class="muted-text">（保存后统一转为字典形式）</span></label><textarea class="editor vault-value-input" id="cookie-value" spellcheck="false" placeholder='name=value; name2=value2 或 {"name": "value"}'></textarea></div>
            <div class="status" id="cookie-dialog-status" role="status" aria-live="polite"></div>
            <div class="risk-actions"><button class="btn" type="button" data-close>取消</button><button class="btn primary" id="cookie-save" type="button">${editing ? "保存修改" : "保存"}</button></div>
        </section>`;
    document.body.appendChild(modal);
    const close = () => modal.remove();
    modal.querySelectorAll("[data-close]").forEach(node => { node.onclick = close; });
    const onKeydown = event => { if (event.key === "Escape") { event.preventDefault(); close(); } };
    document.addEventListener("keydown", onKeydown);
    const editor = cookieTagEditor(options.cookie?.tags || options.tags || []);
    $("#cookie-name").value = options.cookie?.name || options.name || "";
    $("#cookie-domain").value = options.cookie?.domain || options.domain || "";
    $("#cookie-value").value = options.cookie?.value || options.value || "";
    $("#cookie-save").onclick = async () => {
        const status = $("#cookie-dialog-status");
        const name = $("#cookie-name").value.trim();
        const value = $("#cookie-value").value.trim();
        if (!name) { status.className = "status error"; status.textContent = "请填写名称"; return; }
        if (!value) { status.className = "status error"; status.textContent = "Cookie 内容不能为空"; return; }
        $("#cookie-save").disabled = true;
        status.className = "status"; status.textContent = "正在加密保存…";
        const data = await vaultApi("/api/vault/save", {
            id: options.cookie?.id || "",
            name,
            domain: $("#cookie-domain").value.trim(),
            tags: editor.get(),
            value,
        });
        $("#cookie-save").disabled = false;
        if (!data.success) {
            status.className = "status error";
            status.textContent = data.status === 401 ? "收藏夹已锁定，请先解锁（可在「Cookie 收藏夹」页操作）" : data.error;
            return;
        }
        document.removeEventListener("keydown", onKeydown);
        close();
        showToast(editing ? "已更新（重新加密）" : "已加密保存");
        options.onSaved?.();
    };
    $("#cookie-name").focus();
}

async function ensureVaultUnlocked() {
    const state = await vaultApi("/api/vault/status");
    if (!state.success) { showToast(state.error); return false; }
    if (!state.enabled) { showToast("收藏夹未启用"); return false; }
    if (state.initialized && state.unlocked) {
        // 服务端处于解锁态，但本页可能还没有票据（刷新过页面）——
        // 用签名换一张，换不到才退回密码弹窗。
        if (vaultToken || (await vaultAttach())) return true;
    }
    return await new Promise(resolve => {
        const modal = document.createElement("div");
        modal.className = "risk-modal";
        modal.innerHTML = `
            <div class="risk-backdrop" data-close></div>
            <section class="risk-dialog" role="dialog" aria-modal="true" aria-labelledby="vault-gate-title">
                <h2 id="vault-gate-title">${state.initialized ? "解锁 Cookie 收藏夹" : "先设置主密码"}</h2>
                <p class="risk-note">${state.initialized ? "Cookie 内容以密文保存，需要主密码才能写入。" : "首次使用需要设置主密码，之后每次会话输入一次。忘记无法找回。"}</p>
                <div class="field"><label for="vault-gate-pass">主密码</label><input type="password" id="vault-gate-pass" autocomplete="${state.initialized ? "current-password" : "new-password"}"></div>
                <p class="vault-pass-hint" id="vault-gate-hint" aria-live="polite"></p>
                ${state.initialized ? "" : '<div class="field"><label for="vault-gate-pass2">再输入一次</label><input type="password" id="vault-gate-pass2" autocomplete="new-password"></div>'}
                <div class="status" id="vault-gate-msg" role="status" aria-live="polite"></div>
                <div class="risk-actions"><button class="btn" type="button" data-close>取消</button><button class="btn primary" id="vault-gate-ok" type="button">${state.initialized ? "解锁" : "创建并继续"}</button></div>
            </section>`;
        document.body.appendChild(modal);
        const done = value => { modal.remove(); resolve(value); };
        modal.querySelectorAll("[data-close]").forEach(node => { node.onclick = () => done(false); });
        $("#vault-gate-pass").focus();
        vaultPasswordHint($("#vault-gate-pass"), $("#vault-gate-hint"));
        const submit = async () => {
            const password = $("#vault-gate-pass").value;
            const msg = $("#vault-gate-msg");
            if (!state.initialized) {
                if (password !== $("#vault-gate-pass2").value) { msg.className = "status error"; msg.textContent = "两次输入的密码不一致"; return; }
            }
            if (!password) { msg.className = "status error"; msg.textContent = "请输入主密码"; return; }
            msg.textContent = "正在处理…";
            const data = await vaultApi(state.initialized ? "/api/vault/unlock" : "/api/vault/init", { password });
            if (!data.success) { msg.className = "status error"; msg.textContent = data.error; return; }
            vaultToken = data.token;
            done(true);
        };
        $("#vault-gate-ok").onclick = submit;
        $("#vault-gate-pass").addEventListener("keydown", event => { if (event.key === "Enter") submit(); });
    });
}

async function saveCookieFromCurl() {
    const curl = $("#tool-input").value;
    if (!curl.trim()) { showToast("请先输入 curl 命令"); return; }
    const parsed = await vaultApi("/api/vault/from_curl", { curl });
    if (!parsed.success) { showToast(parsed.error); return; }
    if (!parsed.result.cookie) { showToast("这条 curl 里没有 Cookie，可手动填写"); }
    if (!(await ensureVaultUnlocked())) return;
    openCookieDialog({
        name: parsed.result.name,
        domain: parsed.result.domain,
        value: parsed.result.cookie,
        tags: [],
        onSaved: () => showToast("已加密保存到收藏夹"),
    });
}

async function copyCookieFromCurl() {
    const curl = $("#tool-input").value;
    if (!curl.trim()) { showToast("请先输入 curl 命令"); return; }
    const parsed = await vaultApi("/api/vault/from_curl", { curl });
    if (!parsed.success) { showToast(parsed.error); return; }
    if (!parsed.result.cookie) { showToast("这条 curl 里没有 Cookie"); return; }
    copyText(parsed.result.cookie);
}

function renderFormatter(type) {
    const meta = {
        json: ["JSON 格式化", "", "格式化工具", "/api/format/json"],
        header: ["Header 整理", "将 Header 文本转换为字典，并提示无法识别的行。", "格式化工具", "/api/format/header"],
        cookie: ["Cookie 格式化", "", "格式化工具", "/api/format/cookie"],
        dict: ["Dict 格式化", "安全解析 Python 字典、列表或元组。", "格式化工具", "/api/format/dict"],
        javascript: ["JavaScript 整理", "清理 JavaScript 文本的空行和首尾空白。", "格式化工具", "/api/format/javascript"],
        html: ["HTML 整理", "清理 HTML 文本的空行和首尾空白。", "格式化工具", "/api/format/html"],
    }[type];
    if (!meta) {
        renderHome();
        showToast("工具不存在");
        return;
    }
    setGuideState("idle", "输入内容后，工具会在本地处理。");
    workShell(meta[0], meta[1], meta[2], `<div class="toolbar"><button class="btn" id="clear-tool" type="button">清空</button></div><div class="work-grid"><section class="panel"><div class="panel-head">输入内容</div><div class="panel-body"><textarea class="editor" id="tool-input" aria-label="输入内容"></textarea></div></section><section class="panel"><div class="panel-head">处理结果<button id="copy-output" class="link-button" type="button">复制结果</button></div><div class="panel-body"><pre class="output" id="tool-output" aria-label="处理结果"></pre><div class="warning-box" id="formatter-warning" hidden role="alert"></div><div class="status" id="tool-status" role="status" aria-live="polite" aria-atomic="true"></div></div></section></div>`, { compact: ["JSON 格式化", "Cookie 格式化"].includes(meta[0]) });
    const runFormatter = async () => {
        const input = $("#tool-input");
        if (!input.value.trim()) { $("#tool-output").textContent = ""; setStatus($("#tool-status"), "idle", "等待输入内容"); return; }
        setGuideState("loading", "正在处理输入内容。");
        const data = await api(meta[3], { text: input.value });
        if (input.value !== $("#tool-input").value) return;
        $("#tool-output").textContent = data.success ? (typeof data.result === "string" ? data.result : JSON.stringify(data.result, null, 2)) : data.error;
        const state = data.success ? (data.warnings?.length ? "warning" : "success") : "error";
        const message = data.success ? "处理成功" : "处理失败";
        setStatus($("#tool-status"), state, message);
        const warning = $("#formatter-warning");
        warning.hidden = !(data.success && data.warnings?.length);
        warning.textContent = data.warnings?.join("\n") || "";
    };
    bindAutoProcess([$("#tool-input")], runFormatter);
    $("#clear-tool").onclick = () => { $("#tool-input").value = ""; $("#tool-output").textContent = ""; $("#formatter-warning").hidden = true; $("#formatter-warning").textContent = ""; setStatus($("#tool-status"), "recovery", "已清空，可以重新输入"); };
    $("#copy-output").onclick = () => copyText($("#tool-output").textContent);
}

function renderUrl(type) {
    const title = { parse: "URL 参数提取", encode: "URL 编码", decode: "URL 解码" }[type];
    const endpoint = { parse: "/api/url/parse", encode: "/api/url/encode", decode: "/api/url/decode" }[type];
    if (!title || !endpoint) {
        renderHome();
        showToast("工具不存在");
        return;
    }
    workShell(title, "处理 URL、路径和查询参数，结果仅在本地运行。", "URL 工具", `<div class="toolbar"><button class="btn" id="clear-tool" type="button">清空</button></div><div class="work-grid"><section class="panel"><div class="panel-head">输入内容</div><div class="panel-body"><textarea class="editor" id="tool-input" aria-label="输入 URL 或文本" placeholder="https://example.com/search?q=curl2py&page=1"></textarea></div></section><section class="panel"><div class="panel-head">处理结果<button id="copy-output" class="link-button" type="button">复制结果</button></div><div class="panel-body"><pre class="output" id="tool-output" aria-label="URL 处理结果"></pre><div class="status" id="tool-status" role="status" aria-live="polite" aria-atomic="true"></div></div></section></div>`);
    const runUrl = async () => {
        const input = $("#tool-input");
        if (!input.value.trim()) { $("#tool-output").textContent = ""; setStatus($("#tool-status"), "idle", "等待输入内容"); return; }
        setGuideState("loading", "正在处理输入内容。");
        const field = type === "parse" ? "url" : "text";
        const data = await api(endpoint, { [field]: input.value });
        if (input.value !== $("#tool-input").value) return;
        $("#tool-output").textContent = data.success ? JSON.stringify(data.result, null, 2) : data.error;
        setStatus($("#tool-status"), data.success ? "success" : "error", data.success ? "处理成功" : data.error);
    };
    bindAutoProcess([$("#tool-input")], runUrl);
    $("#clear-tool").onclick = () => { $("#tool-input").value = ""; $("#tool-output").textContent = ""; setStatus($("#tool-status"), "recovery", "已清空，可以重新输入"); };
    $("#copy-output").onclick = () => copyText($("#tool-output").textContent);
}

function createDiffEditorPair(container, leftInput, rightInput, visualLines, onMerge) {
    [leftInput, rightInput].forEach(input => {
        input.spellcheck = false;
        input.setAttribute("spellcheck", "false");
        input.setAttribute("autocorrect", "off");
        input.setAttribute("autocapitalize", "off");
    });
    container.textContent = "";
    container.classList.add("diff-editor-grid");
    const grid = container;
    const leftShell = document.createElement("div"); leftShell.className = "diff-editor-shell";
    const rightShell = document.createElement("div"); rightShell.className = "diff-editor-shell";
    const gutter = document.createElement("div"); gutter.className = "diff-connector";
    const leftSurface = document.createElement("div"); leftSurface.className = "diff-surface";
    const rightSurface = document.createElement("div"); rightSurface.className = "diff-surface";
    const leftHead = document.createElement("div"); leftHead.className = "panel-head"; leftHead.textContent = "原文";
    const rightHead = document.createElement("div"); rightHead.className = "panel-head"; rightHead.textContent = "修改文本";
    const gutterHead = document.createElement("div"); gutterHead.className = "connector-head"; gutterHead.setAttribute("aria-hidden", "true");
    gutter.appendChild(gutterHead);
    leftShell.append(leftHead, leftInput, leftSurface);
    rightShell.append(rightHead, rightInput, rightSurface);
    grid.append(leftShell, gutter, rightShell);
    const leftHeadHeight = leftHead.getBoundingClientRect().height;
    const rightHeadHeight = rightHead.getBoundingClientRect().height;
    leftShell.style.setProperty("--diff-head-height", `${leftHeadHeight}px`);
    rightShell.style.setProperty("--diff-head-height", `${rightHeadHeight}px`);
    gutterHead.style.height = `${Math.max(leftHeadHeight, rightHeadHeight)}px`;
    gutterHead.style.flexBasis = `${Math.max(leftHeadHeight, rightHeadHeight)}px`;
    leftInput.__diffSyncSurface = leftSurface;
    rightInput.__diffSyncSurface = rightSurface;
    const lineHeight = parseFloat(getComputedStyle(leftInput).lineHeight) || 18;
    const mapScroll = (source, target) => {
        if (grid.__diffSyncing) return;
        grid.__diffSyncing = true;
        const sourceSurface = source.__diffSyncSurface;
        const targetSurface = target.__diffSyncSurface;
        const sourceLine = Math.max(1, Math.floor((source.scrollTop + 10) / lineHeight) + 1);
        const targetRows = [...targetSurface.children];
        const side = target === leftInput ? "left_line" : "right_line";
        let row = targetRows.find(node => Number(node.dataset.rawLine) >= sourceLine) || targetRows[targetRows.length - 1];
        const targetTop = row ? Math.max(0, row.offsetTop - 10) : 0;
        target.scrollTop = targetTop;
        target.scrollLeft = source.scrollLeft;
        targetSurface.scrollTop = targetTop;
        targetSurface.scrollLeft = source.scrollLeft;
        sourceSurface.scrollTop = source.scrollTop;
        sourceSurface.scrollLeft = source.scrollLeft;
        requestAnimationFrame(() => { grid.__diffSyncing = false; });
    };
    if (!leftInput.__diffScrollBound) {
        leftInput.addEventListener("scroll", () => mapScroll(leftInput, rightInput), { passive: true });
        rightInput.addEventListener("scroll", () => mapScroll(rightInput, leftInput), { passive: true });
        leftInput.__diffScrollBound = rightInput.__diffScrollBound = true;
    }

    const renderSide = (surface, side) => {
        surface.textContent = "";
        (visualLines || []).forEach(line => {
            const row = document.createElement("div");
            row.className = `diff-surface-line ${line.connector?.type || "equal"}`;
            row.dataset.lineId = line.id || "";
            row.dataset.rawLine = String(line[side === "left" ? "left_line" : "right_line"] || 0);
            const number = document.createElement("span");
            number.className = "diff-number";
            number.textContent = line[side === "left" ? "left_line" : "right_line"] ?? "";
            const text = document.createElement("span");
            text.className = "diff-text";
            (line[side === "left" ? "left_segments" : "right_segments"] || []).forEach(segment => {
                const node = document.createElement("span");
                node.className = `diff-segment ${segment.type}`;
                node.textContent = segment.text || "";
                text.appendChild(node);
            });
            row.append(number, text);
            surface.appendChild(row);
        });
    };
    renderSide(leftSurface, "left");
    renderSide(rightSurface, "right");

    (visualLines || []).forEach(line => {
        const row = document.createElement("div");
        row.className = "connector-row visual-diff-row";
        row.dataset.lineId = line.id || "";
        row.setAttribute("aria-hidden", "true");
        gutter.appendChild(row);
    });
}
function renderJsonDiff() {
    workShell("JSON 对比", "", "常用工具", `<div class="toolbar"><button class="btn" id="clear-tool" type="button">清空</button></div><div class="diff-workspace" aria-label="JSON 双栏差异编辑器"><div class="diff-editor-shell"><div class="panel-head">原始 JSON</div><textarea class="editor diff-editor" id="left-input" aria-label="原始 JSON" spellcheck="false" autocorrect="off" autocapitalize="off">{"name":"old","page":1}</textarea><div class="diff-surface" id="left-surface"></div></div><div class="diff-connector" id="json-connector" aria-label="差异连接区"></div><div class="diff-editor-shell"><div class="panel-head">新 JSON</div><textarea class="editor diff-editor" id="right-input" aria-label="新 JSON" spellcheck="false" autocorrect="off" autocapitalize="off">{"name":"new","page":2}</textarea><div class="diff-surface" id="right-surface"></div></div></div><div class="status" id="tool-status" role="status" aria-live="polite" aria-atomic="true"></div>`, { compact: true });
    const leftInput=$("#left-input"),rightInput=$("#right-input"),workspace=$(".diff-workspace");let requestId=0;
    const run=async()=>{const left=leftInput.value,right=rightInput.value,current=++requestId;const oversized=rejectOversizedText(left, DIFF_MAX_CHARS, "左侧")||rejectOversizedText(right, DIFF_MAX_CHARS, "右侧");if(oversized){if(current!==requestId)return;setStatus($("#diff-status"),"error",oversized);setGuideState("error",oversized);return;}const selection={left:[leftInput.selectionStart,leftInput.selectionEnd,leftInput.scrollTop,leftInput.scrollLeft],right:[rightInput.selectionStart,rightInput.selectionEnd,rightInput.scrollTop,rightInput.scrollLeft]};if(!left.trim()||!right.trim()){workspace.querySelectorAll(".diff-surface,.diff-connector").forEach(n=>n.textContent="");setStatus($("#tool-status"),"idle","等待两侧输入");return;}const data=await api("/api/json/diff",{left,right});if(current!==requestId)return;if(!data.success){setStatus($("#tool-status"),"error",data.error);return;}if(leftInput.value===left){leftInput.value=data.formatted_left;leftInput.setSelectionRange(Math.min(selection.left[0],leftInput.value.length),Math.min(selection.left[1],leftInput.value.length));leftInput.scrollTop=selection.left[2];leftInput.scrollLeft=selection.left[3];}if(rightInput.value===right){rightInput.value=data.formatted_right;rightInput.setSelectionRange(Math.min(selection.right[0],rightInput.value.length),Math.min(selection.right[1],rightInput.value.length));rightInput.scrollTop=selection.right[2];rightInput.scrollLeft=selection.right[3];}createDiffEditorPair(workspace,leftInput,rightInput,data.visual_lines,(line,action)=>mergeLine(line,action,leftInput,rightInput,run,data.visual_lines));const diffMessage=data.stats?.modified||data.stats?.insert||data.stats?.delete?"对比完成":"两侧内容一致";setStatus($("#tool-status"),"success",diffMessage);};
    bindAutoProcess([leftInput,rightInput],run);$("#clear-tool").onclick=()=>{requestId++;leftInput.value="";rightInput.value="";workspace.querySelectorAll(".diff-surface,.diff-connector").forEach(n=>n.textContent="");setStatus($("#tool-status"),"recovery","已清空，可以重新输入");};run();
}
function mergeLine(line, action, leftInput, rightInput, run) {
    const target = action === "left_to_right" ? rightInput : leftInput;
    const sourceValue = action === "left_to_right" ? line.left_text : line.right_text;
    const targetLine = action === "left_to_right" ? line.right_line : line.left_line;
    const sourceLine = action === "left_to_right" ? line.left_line : line.right_line;
    const values = target.value.split(/\r?\n/);
    if (targetLine != null) {
        if (sourceLine == null || sourceValue == null) values.splice(Math.max(0, targetLine - 1), 1);
        else values[Math.max(0, targetLine - 1)] = sourceValue;
    } else if (sourceValue != null) {
        const insertAt = Math.max(0, (sourceLine || values.length + 1) - 1);
        values.splice(Math.min(insertAt, values.length), 0, sourceValue);
    }
    target.value = values.join("\n");
    run();
}
function mergeDiffLine(line, action, leftInput, rightInput, run){ mergeLine(line, action, leftInput, rightInput, run); }
function renderTextDiff() {
    workShell("文本对比", "", "文本工具", `<div class="toolbar"><button class="btn" id="clear-tool" type="button">清空</button></div><div class="diff-workspace" aria-label="文本双栏差异编辑器"><div class="diff-editor-shell"><div class="panel-head">原文本</div><textarea class="editor diff-editor" id="left-input" aria-label="原文本" spellcheck="false" autocorrect="off" autocapitalize="off"></textarea><div class="diff-surface" id="left-surface"></div></div><div class="diff-connector" id="text-connector" aria-label="差异连接区"></div><div class="diff-editor-shell"><div class="panel-head">新文本</div><textarea class="editor diff-editor" id="right-input" aria-label="新文本" spellcheck="false" autocorrect="off" autocapitalize="off"></textarea><div class="diff-surface" id="right-surface"></div></div></div><div class="status" id="tool-status" role="status" aria-live="polite" aria-atomic="true"></div>`, { compact: true });
    const leftInput=$("#left-input"),rightInput=$("#right-input"),workspace=$(".diff-workspace");let requestId=0;
    const run=async()=>{const left=leftInput.value,right=rightInput.value,current=++requestId;const oversized=rejectOversizedText(left, DIFF_MAX_CHARS, "左侧")||rejectOversizedText(right, DIFF_MAX_CHARS, "右侧");if(oversized){if(current!==requestId)return;setStatus($("#diff-status"),"error",oversized);setGuideState("error",oversized);return;}const selection={left:[leftInput.selectionStart,leftInput.selectionEnd,leftInput.scrollTop,leftInput.scrollLeft],right:[rightInput.selectionStart,rightInput.selectionEnd,rightInput.scrollTop,rightInput.scrollLeft]};if(!left.trim()&&!right.trim()){workspace.querySelectorAll(".diff-surface,.diff-connector").forEach(n=>n.textContent="");setStatus($("#tool-status"),"recovery","已清空，可以重新输入");return;}const data=await api("/api/text/diff",{left,right});if(current!==requestId)return;if(!data.success){setStatus($("#tool-status"),"error",data.error);return;}createDiffEditorPair(workspace,leftInput,rightInput,data.visual_lines,(line,action)=>mergeLine(line,action,leftInput,rightInput,run));setStatus($("#tool-status"),"success","对比完成");};bindAutoProcess([leftInput,rightInput],run);$("#clear-tool").onclick=()=>{requestId++;leftInput.value="";rightInput.value="";workspace.querySelectorAll(".diff-surface,.diff-connector").forEach(n=>n.textContent="");setStatus($("#tool-status"),"recovery","已清空，可以重新输入");};
}

function renderTime() {
    const timezoneOptions = ["Asia/Shanghai", "UTC", "America/Los_Angeles", "Europe/London"];
    const timezoneSelect = `<div class="field"><label>时区</label><select id="timezone-select">${timezoneOptions.map(zone => `<option value="${zone}">${zone}</option>`).join("")}</select></div>`;
    workShell("时间转换", "支持秒、毫秒时间戳与日期时间的互相转换，支持常用时区和 ISO-8601 日期。", "常用工具", `<div class="toolbar"></div><div class="work-grid"><section class="panel"><div class="panel-head">时间戳转日期</div><div class="panel-body"><div class="field"><label>时间戳</label><input id="timestamp-input" value="1780499417"></div><div class="field"><label>单位</label><select id="timestamp-unit"><option value="s">秒</option><option value="ms">毫秒</option></select></div>${timezoneSelect}<pre class="output" id="timestamp-output"></pre></div></section><section class="panel"><div class="panel-head">日期转时间戳</div><div class="panel-body"><div class="field"><label>日期时间</label><input id="datetime-input" value="2026-06-03 23:10:17"></div><div class="field"><label>单位</label><select id="datetime-unit"><option value="s">秒</option><option value="ms">毫秒</option></select></div><div class="field"><label>时区</label><select id="datetime-timezone">${timezoneOptions.map(zone => `<option value="${zone}">${zone}</option>`).join("")}</select></div><pre class="output" id="datetime-output"></pre></div></section></div>`);
    let timestampRequestId = 0;
    let datetimeRequestId = 0;
    let timestampController = null;
    let datetimeController = null;
    const runTimestamp = async () => {
        const input = $("#timestamp-input");
        const unit = $("#timestamp-unit");
        const timezone = $("#timezone-select");
        const snapshot = { value: input.value, unit: unit.value, tz: timezone.value };
        setGuideState("loading", "正在转换时间值。");
        const current = ++timestampRequestId;
        if (timestampController) timestampController.abort();
        timestampController = null;
        if (!snapshot.value.trim()) { $("#timestamp-output").textContent = ""; return; }
        const controller = new AbortController();
        timestampController = controller;
        const isCurrent = () => current === timestampRequestId
            && input === $("#timestamp-input") && unit === $("#timestamp-unit") && timezone === $("#timezone-select")
            && snapshot.value === input.value && snapshot.unit === unit.value && snapshot.tz === timezone.value;
        try {
            const data = await api("/api/time/timestamp", { timestamp: snapshot.value, unit: snapshot.unit, tz: snapshot.tz }, controller.signal);
            if (!isCurrent() || data.aborted) return;
            $("#timestamp-output").textContent = data.success ? data.result : data.error;
            setGuideState(data.success ? "success" : "error", data.success ? "时间转换完成" : data.error);
        } catch (error) {
            if (!isCurrent() || error.name === "AbortError") return;
            $("#timestamp-output").textContent = error.message || "服务暂时不可用";
            setGuideState("error", $("#timestamp-output").textContent);
        } finally {
            if (timestampController === controller) timestampController = null;
        }
    };
    const runDatetime = async () => {
        const input = $("#datetime-input");
        const unit = $("#datetime-unit");
        const timezone = $("#datetime-timezone");
        const snapshot = { value: input.value, unit: unit.value, tz: timezone.value };
        setGuideState("loading", "正在转换日期时间。");
        const current = ++datetimeRequestId;
        if (datetimeController) datetimeController.abort();
        datetimeController = null;
        if (!snapshot.value.trim()) { $("#datetime-output").textContent = ""; return; }
        const controller = new AbortController();
        datetimeController = controller;
        const isCurrent = () => current === datetimeRequestId
            && input === $("#datetime-input") && unit === $("#datetime-unit") && timezone === $("#datetime-timezone")
            && snapshot.value === input.value && snapshot.unit === unit.value && snapshot.tz === timezone.value;
        try {
            const data = await api("/api/time/datetime", { datetime: snapshot.value, unit: snapshot.unit, tz: snapshot.tz }, controller.signal);
            if (!isCurrent() || data.aborted) return;
            $("#datetime-output").textContent = data.success ? data.result : data.error;
            setGuideState(data.success ? "success" : "error", data.success ? "日期转换完成" : data.error);
        } catch (error) {
            if (!isCurrent() || error.name === "AbortError") return;
            $("#datetime-output").textContent = error.message || "服务暂时不可用";
            setGuideState("error", $("#datetime-output").textContent);
        } finally {
            if (datetimeController === controller) datetimeController = null;
        }
    };
    bindAutoProcess([$("#timestamp-input"), $("#timestamp-unit"), $("#timezone-select")], runTimestamp);
    bindAutoProcess([$("#datetime-input"), $("#datetime-unit"), $("#datetime-timezone")], runDatetime);
    runTimestamp(); runDatetime();
}

function renderHtml() {
    workShell("HTML 渲染", "在隔离的预览区查看 HTML 片段。", "文本工具", `<div class="toolbar"><button class="btn" id="clear-tool" type="button">清空</button></div><div class="work-grid"><section class="panel"><div class="panel-head">HTML 输入</div><div class="panel-body"><textarea class="editor" id="tool-input"><h1>Hello Spider</h1><p>本地预览</p></textarea></div></section><section class="panel"><div class="panel-head">预览结果</div><div class="panel-body"><iframe id="html-preview" title="HTML 预览" sandbox="allow-same-origin" style="width:100%;min-height:390px;border:0;border-radius:12px;background:var(--surface);box-shadow:inset 3px 3px 6px var(--sh-dark),inset -3px -3px 6px var(--sh-light)"></iframe></div></section></div>`);
    const previewHtml = () => { $("#html-preview").srcdoc = $("#tool-input").value; };
    bindAutoProcess([$("#tool-input")], previewHtml);
    previewHtml();
    $("#clear-tool").onclick = () => { $("#tool-input").value = ""; $("#html-preview").srcdoc = ""; };
}

function renderCrypto(type) {
    const isBase64 = type === "base64";
    const title = isBase64 ? "Base64 编码解码" : "摘要计算";
    const description = isBase64 ? "对文本进行 Base64 编码或解码。" : "计算文本的 MD5、SHA-1 或 SHA-256 摘要。";
    const control = isBase64 ? `<select id="crypto-option"><option value="encode">编码</option><option value="decode">解码</option></select>` : `<select id="crypto-option"><option value="md5">MD5</option><option value="sha1">SHA-1</option><option value="sha256">SHA-256</option></select>`;
    workShell(title, description, "加解密工具", `<div class="toolbar"><button class="btn" id="clear-tool" type="button">清空</button></div><div class="work-grid"><section class="panel"><div class="panel-head">输入文本</div><div class="panel-body"><div class="field"><label>处理方式</label>${control}</div><textarea class="editor" id="tool-input" aria-label="输入文本"></textarea></div></section><section class="panel"><div class="panel-head">结果<button id="copy-output" class="link-button" type="button">复制结果</button></div><div class="panel-body"><pre class="output" id="tool-output" aria-label="加解密结果"></pre><div class="status" id="tool-status" role="status" aria-live="polite" aria-atomic="true"></div></div></section></div>`);
    let cryptoRequestId = 0;
    let cryptoController = null;
    const runCrypto = async () => {
        const input = $("#tool-input");
        const option = $("#crypto-option");
        const snapshot = { text: input.value, option: option.value };
        const current = ++cryptoRequestId;
        if (cryptoController) cryptoController.abort();
        cryptoController = null;
    if (!snapshot.text.trim()) { $("#tool-output").textContent = ""; setStatus($("#tool-status"), "idle", "等待输入文本"); return; }
        const controller = new AbortController();
        cryptoController = controller;
        const endpoint = isBase64 ? "/api/crypto/base64" : "/api/crypto/hash";
        const key = isBase64 ? "operation" : "algorithm";
        const isCurrent = () => current === cryptoRequestId
            && input === $("#tool-input") && option === $("#crypto-option")
            && snapshot.text === input.value && snapshot.option === option.value;
        try {
            const data = await api(endpoint, { text: snapshot.text, [key]: snapshot.option }, controller.signal);
            if (!isCurrent() || data.aborted) return;
            $("#tool-output").textContent = data.success ? data.result : data.error;
            setStatus($("#tool-status"), data.success ? "success" : "error", data.success ? "处理成功" : data.error);
        } catch (error) {
            if (!isCurrent() || error.name === "AbortError") return;
            $("#tool-output").textContent = "";
            setStatus($("#tool-status"), "error", error.message || "服务暂时不可用");
        } finally {
            if (cryptoController === controller) cryptoController = null;
        }
    };
    bindAutoProcess([$("#tool-input"), $("#crypto-option")], runCrypto);
    $("#clear-tool").onclick = () => {
        cryptoRequestId += 1;
        if (cryptoController) cryptoController.abort();
        cryptoController = null;
        $("#tool-input").value = ""; $("#tool-output").textContent = ""; setStatus($("#tool-status"), "recovery", "已清空，可以重新输入");
    };
    $("#copy-output").onclick = () => copyText($("#tool-output").textContent);
}

const cryptoGroups = {
    symmetric: ["AES", "Blowfish", "DES", "TripleDES", "RC2", "RC4", "RC4Drop", "ChaCha20"],
    digest: ["MD5", "SHA1", "SHA224", "SHA256", "SHA384", "SHA512", "SHA3-224", "SHA3-256", "SHA3-384", "SHA3-512", "RIPEMD160", "SM3"],
    hmac: ["HmacMD5", "HmacSHA1", "HmacSHA224", "HmacSHA256", "HmacSHA384", "HmacSHA512"],
    encoding: ["Utf8", "Utf16", "Utf16LE", "Latin1", "Hex", "Base64", "Base64 URL Safe"],
};

function cryptoOptions(group) {
    return cryptoGroups[group].map(item => `<option value="${item}">${item}</option>`).join("");
}

function renderCryptoWorkbench() {
    workShell("加解密工具", "参考 CryptoJS 常用分类，支持摘要、HMAC、字符编码和常用对称加密。", "加解密工具", `<div class="toolbar"><button class="btn" id="crypto-clear" type="button">清空</button></div><div class="work-grid"><section class="panel"><div class="panel-head">输入</div><div class="panel-body"><div class="field"><label>类型</label><select id="crypto-group" aria-label="加解密类型"><option value="digest">摘要</option><option value="hmac">HMAC</option><option value="encoding">字符编码</option><option value="symmetric">对称加密</option></select></div><div class="field"><label>算法</label><select id="crypto-operation" aria-label="加解密算法"></select></div><div class="field" id="crypto-action-field"><label>操作</label><select id="crypto-action" aria-label="加解密操作"><option value="digest">计算</option><option value="encode">编码</option><option value="decode">解码</option></select></div><div class="field"><label>输入编码</label><select id="crypto-input-format" aria-label="输入编码"><option>Utf8</option><option>Hex</option><option>Base64</option><option>Base64 URL Safe</option><option>Latin1</option></select></div><div class="field" id="crypto-output-field"><label>输出编码</label><select id="crypto-output-format" aria-label="输出编码"><option>Base64</option><option>Hex</option><option>Utf8</option><option>Base64 URL Safe</option><option>Latin1</option></select></div><div class="field" id="crypto-key-field"><label>密钥</label><input id="crypto-key" aria-label="密钥" placeholder="AES 请输入 16/24/32 字节密钥"></div><div class="field" id="crypto-iv-field"><label>IV / Nonce</label><input id="crypto-iv" aria-label="IV 或 Nonce" placeholder="CBC 通常需要 16 字节 IV"></div><div class="field" id="crypto-mode-field"><label>模式</label><select id="crypto-mode" aria-label="加密模式"><option>CBC</option><option>ECB</option><option>CFB</option><option>OFB</option><option>CTR</option></select></div><div class="field" id="crypto-padding-field"><label>填充</label><select id="crypto-padding" aria-label="填充方式"><option>Pkcs7</option><option>AnsiX923</option><option>Iso97971</option><option>ZeroPadding</option><option>NoPadding</option></select></div><div class="field"><label>文本</label><textarea class="editor compact-editor" id="crypto-input" aria-label="待处理文本" placeholder="请输入文本"></textarea></div></div></section><section class="panel"><div class="panel-head">处理结果<button id="crypto-copy" class="link-button" type="button">复制结果</button></div><div class="panel-body"><pre class="output" id="crypto-output" aria-label="加解密处理结果"></pre><div class="status" id="crypto-status" role="status" aria-live="polite" aria-atomic="true"></div></div></section></div>`);
    const group = $("#crypto-group");
    const operation = $("#crypto-operation");
    const updateControls = () => {
        operation.innerHTML = cryptoOptions(group.value);
        const selected = group.value;
        const isEncoding = selected === "encoding";
        const isDigest = selected === "digest";
        const isHmac = selected === "hmac";
        $("#crypto-action-field").hidden = isDigest;
        $("#crypto-key-field").hidden = !isHmac && !["symmetric"].includes(selected);
        $("#crypto-iv-field").hidden = selected !== "symmetric";
        $("#crypto-mode-field").hidden = selected !== "symmetric";
        $("#crypto-padding-field").hidden = selected !== "symmetric";
        $("#crypto-output-field").hidden = isDigest || isHmac || (!isEncoding && selected !== "symmetric");
        if (isEncoding) $("#crypto-action").innerHTML = `<option value="encode">编码</option><option value="decode">解码</option>`;
        else if (!isDigest) $("#crypto-action").innerHTML = `<option value="encrypt">加密</option><option value="decrypt">解密</option>`;
    };
    group.onchange = updateControls;
    updateControls();
    let workbenchRequestId = 0;
    let workbenchController = null;
    const readWorkbenchSnapshot = () => ({
        group: group.value,
        operation: operation.value,
        action: $("#crypto-action").value,
        input_format: $("#crypto-input-format").value,
        output_format: $("#crypto-output-format").value,
        key: $("#crypto-key").value,
        iv: $("#crypto-iv").value,
        mode: $("#crypto-mode").value,
        padding: $("#crypto-padding").value,
        input: $("#crypto-input").value,
    });
    const runWorkbench = async () => {
        const snapshot = readWorkbenchSnapshot();
        const current = ++workbenchRequestId;
        if (workbenchController) workbenchController.abort();
        workbenchController = null;
        if (!snapshot.input.trim()) { $("#crypto-output").textContent = ""; setStatus($("#crypto-status"), "idle", "等待输入文本"); return; }
        const controller = new AbortController();
        workbenchController = controller;
        const isCurrent = () => current === workbenchRequestId && JSON.stringify(snapshot) === JSON.stringify(readWorkbenchSnapshot());
        try {
            const data = await api("/api/crypto/process", {
                operation: snapshot.operation,
                action: snapshot.action,
                text: snapshot.input,
                key: snapshot.key,
                iv: snapshot.iv,
                mode: snapshot.mode,
                padding: snapshot.padding,
                input_format: snapshot.input_format,
                output_format: snapshot.output_format,
            }, controller.signal);
            if (!isCurrent() || data.aborted) return;
            $("#crypto-output").textContent = data.success ? data.result : data.error;
            setStatus($("#crypto-status"), data.success ? "success" : "error", data.success ? "处理成功" : data.error);
        } catch (error) {
            if (!isCurrent() || error.name === "AbortError") return;
            $("#crypto-output").textContent = "";
            setStatus($("#crypto-status"), "error", error.message || "服务暂时不可用");
        } finally {
            if (workbenchController === controller) workbenchController = null;
        }
    };
    bindAutoProcess([group, operation, $("#crypto-action"), $("#crypto-input-format"), $("#crypto-output-format"), $("#crypto-key"), $("#crypto-iv"), $("#crypto-mode"), $("#crypto-padding"), $("#crypto-input")], runWorkbench);
    group.addEventListener("change", () => { updateControls(); });
    $("#crypto-clear").onclick = () => {
        workbenchRequestId += 1;
        if (workbenchController) workbenchController.abort();
        workbenchController = null;
        $("#crypto-input").value = ""; $("#crypto-output").textContent = ""; $("#crypto-key").value = ""; $("#crypto-iv").value = ""; $("#crypto-status").textContent = "";
    };
    $("#crypto-copy").onclick = () => copyText($("#crypto-output").textContent);
}

function renderImageBase64() {
    workShell("图片转 Base64", "支持单张或批量图片转换，可直接输出 Data URL，也可输出为数组格式。", "图片工具", `<div class="toolbar"><button class="btn" id="image-clear" type="button">清空</button></div><section class="panel"><div class="panel-head">选择图片</div><div class="panel-body"><div class="field"><label for="image-output">输出格式</label><select id="image-output" aria-label="图片输出格式"><option value="direct">直接输出</option><option value="array">数组格式</option></select></div><div id="image-drop" class="image-drop" tabindex="0" role="button" aria-label="点击选择图片，或把图片拖到这里"><p class="image-drop-title">点击选择图片，或把图片拖到这里</p><p class="muted-text">单次最多 10 张 · 单张不超过 100MB · 合计不超过 200MB，支持多选与拖拽</p></div><input id="image-files" type="file" accept="image/*" multiple hidden aria-label="选择图片文件"><div id="image-list" class="image-list"></div><div class="status" id="image-status" role="status" aria-live="polite" aria-atomic="true"></div></div></section><section class="panel"><div class="panel-head">图片预览</div><div class="panel-body"><div id="image-preview" class="image-preview-grid"><p class="muted-text">选择图片后在这里预览。</p></div></div></section><section class="panel"><div class="panel-head">Base64 结果<button id="image-copy" class="link-button" type="button">复制全部</button></div><div class="panel-body"><div id="image-result-list" class="image-result-list"></div><details id="image-raw-wrap" class="image-raw-wrap" hidden><summary>展开原始 Base64 文本</summary><pre class="output image-raw" id="image-result" aria-label="图片 Base64 结果"></pre></details></div></section>`);
    let imageRequestId = 0;
    let imageController = null;
    const selected = [];
    const objectUrls = [];
    const drop = $("#image-drop");
    const input = $("#image-files");
    const esc = text => String(text).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch]));
    const humanSize = value => value >= 1048576 ? `${(value / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(value / 1024))}KB`;
    const renderFiles = () => {
        objectUrls.splice(0).forEach(url => URL.revokeObjectURL(url));
        selected.forEach(file => objectUrls.push(URL.createObjectURL(file)));
        if (!selected.length) {
            $("#image-list").innerHTML = "";
            $("#image-preview").innerHTML = '<p class="muted-text">选择图片后在这里预览。</p>';
            $("#image-result-list").innerHTML = "";
            $("#image-raw-wrap").hidden = true;
            return;
        }
        $("#image-list").innerHTML = selected.map((file, index) => `<div class="image-item"><img class="image-thumb" src="${objectUrls[index]}" alt="${esc(file.name)} 缩略图"><div class="image-meta"><span class="image-name" title="${esc(file.name)}">${esc(file.name)}</span><span class="muted-text">${humanSize(file.size)}</span></div><button class="image-remove" type="button" data-index="${index}" aria-label="移除 ${esc(file.name)}">×</button></div>`).join("");
        $("#image-preview").innerHTML = selected.map((file, index) => `<figure class="image-preview-card"><img src="${objectUrls[index]}" alt="${esc(file.name)} 预览" loading="lazy"><figcaption title="${esc(file.name)}">${esc(file.name)}</figcaption></figure>`).join("");
        $("#image-list").querySelectorAll(".image-remove").forEach(button => {
            button.onclick = () => {
                selected.splice(Number(button.dataset.index), 1);
                syncInput();
                renderFiles();
                runImage();
            };
        });
    };
    const syncInput = () => {
        try {
            const transfer = new DataTransfer();
            selected.forEach(file => transfer.items.add(file));
            input.files = transfer.files;
        } catch { /* 旧浏览器不支持给 input.files 赋值，只影响原生控件展示 */ }
    };
    const acceptFiles = list => {
        const incoming = [...list].filter(file => (file.type || "").startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(file.name || ""));
        if (!incoming.length) { setStatus($("#image-status"), "error", "没有识别到图片文件，请选择 png/jpg/gif/webp/bmp/svg"); return; }
        selected.push(...incoming);
        if (selected.length > 10) {
            selected.length = 10;
            setStatus($("#image-status"), "error", "单次最多 10 张，已保留前 10 张");
        }
        syncInput();
        renderFiles();
        runImage();
    };
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); } });
    input.addEventListener("change", () => acceptFiles(input.files));
    ["dragenter", "dragover"].forEach(type => drop.addEventListener(type, event => { event.preventDefault(); drop.classList.add("dragover"); }));
    ["dragleave", "drop"].forEach(type => drop.addEventListener(type, event => { event.preventDefault(); drop.classList.remove("dragover"); }));
    drop.addEventListener("drop", event => acceptFiles(event.dataTransfer ? event.dataTransfer.files : []));
    const runImage = async () => {
        const files = [...selected];
        const current = ++imageRequestId;
        if (imageController) imageController.abort();
        imageController = null;
        if (!files.length) { $("#image-result-list").innerHTML = ""; $("#image-raw-wrap").hidden = true; $("#image-result").textContent = ""; setGuideState("idle", "选择图片后，工具会在本地处理。"); return; }
        setGuideState("loading", "正在读取图片并生成 Base64。");
        const form = new FormData();
        files.forEach(file => form.append("files", file));
        form.append("output", $("#image-output").value);
        const controller = new AbortController();
        imageController = controller;
        const timeout = setTimeout(() => controller.abort(), 300000);
        try {
            const headers = {};
            if (window.CURL2PY_API_TOKEN) headers["X-API-Token"] = window.CURL2PY_API_TOKEN;
            const response = await fetch("/api/image/base64", { method: "POST", headers, body: form, signal: controller.signal });
            const data = await response.json();
            if (current !== imageRequestId) return;
            if (!data.success) {
                $("#image-result-list").innerHTML = "";
                $("#image-raw-wrap").hidden = true;
                setStatus($("#image-status"), "error", data.error || "转换失败");
                setGuideState("error", data.error || "转换失败");
                return;
            }
            const results = Array.isArray(data.result) ? data.result : [data.result];
            $("#image-result-list").innerHTML = results.map((value, index) => `<div class="image-result-item"><span class="image-name" title="${esc(files[index] ? files[index].name : "")}">${esc(files[index] ? files[index].name : `图片 ${index + 1}`)}</span><span class="muted-text">Base64 ${humanSize(value.length)}</span><button class="link-button" type="button" data-copy="${index}">复制</button></div>`).join("");
            $("#image-result-list").querySelectorAll("[data-copy]").forEach(button => { button.onclick = () => copyText(results[Number(button.dataset.copy)]); });
            $("#image-result").textContent = Array.isArray(data.result) ? JSON.stringify(data.result, null, 2) : data.result;
            $("#image-raw-wrap").hidden = false;
            $("#image-raw-wrap").open = false;
            setStatus($("#image-status"), "success", `转换成功，共 ${data.count} 张；结果较长已折叠，可逐条复制或点开查看`);
            setGuideState("success", $("#image-status").textContent);
        } catch (error) {
            if (current !== imageRequestId) return;
            $("#image-status").textContent = error.name === "AbortError" ? "请求超时或已取消" : "服务暂时不可用";
            $("#image-status").className = "status error";
            setGuideState("error", $("#image-status").textContent);
        } finally {
            clearTimeout(timeout);
            if (imageController === controller) imageController = null;
        }
    };
    $("#image-output").addEventListener("change", () => { if (selected.length) runImage(); });
    $("#image-clear").onclick = () => {
        imageRequestId += 1;
        if (imageController) imageController.abort();
        imageController = null;
        selected.length = 0;
        syncInput();
        input.value = "";
        renderFiles();
        setStatus($("#image-status"), "recovery", "已清空，可以重新选择图片");
        setGuideState("idle", "选择图片后，工具会在本地处理。");
    };
    $("#image-copy").onclick = () => copyText($("#image-result").textContent);
}

/* ===== 主题配色 ===== */
const THEME_KEY = "curl2py.theme.v2";
/* 新拟物要求单一浅灰底面 + 同色系元素, 因此主题只换"灰调 + 强调色",
   不再有深色底。swatch 用各主题的强调色实心色块区分(禁止渐变)。 */
const THEMES = [
    { id: "light", name: "云灰", dot: "#6d5dfc" },
    { id: "mist", name: "冷雾", dot: "#2f7fd4" },
    { id: "sand", name: "暖砂", dot: "#c2703f" },
    { id: "sage", name: "青苔", dot: "#2f8f7a" },
    { id: "lilac", name: "藕荷", dot: "#7c5cd6" },
    { id: "graphite", name: "石墨", dot: "#4a5568" },
];

function themeById(id) {
    return THEMES.find(item => item.id === id) || THEMES[0];
}

function applyTheme(id) {
    const theme = themeById(id);
    document.documentElement.setAttribute("data-theme", theme.id);
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.content = getComputedStyle(document.documentElement).getPropertyValue("--bg-base").trim() || "#edf1f7";
    const dot = $("#theme-toggle-dot");
    if (dot) dot.style.background = theme.dot;
    document.querySelectorAll("[data-theme-option]").forEach(button => {
        const active = button.dataset.themeOption === theme.id;
        button.classList.toggle("active", active);
        button.setAttribute("aria-checked", active ? "true" : "false");
    });
    try { localStorage.setItem(THEME_KEY, theme.id); } catch (_error) { /* 隐私模式下不记忆 */ }
}

function initTheme() {
    const wrap = $(".theme-wrap");
    const list = $("#theme-list");
    const popover = $("#theme-popover");
    const toggle = $("#theme-toggle");
    if (!wrap || !list || !popover || !toggle || wrap.dataset.bound) return;
    wrap.dataset.bound = "true";
    list.innerHTML = THEMES.map(theme =>
        `<button class="theme-option" type="button" role="radio" data-theme-option="${theme.id}"><span class="theme-swatch" style="background:${theme.dot}"></span><span>${theme.name}</span><span class="theme-check">✓</span></button>`
    ).join("");
    let stored = "light";
    try { stored = localStorage.getItem(THEME_KEY) || "light"; } catch (_error) { /* 隐私模式 */ }
    applyTheme(stored);
    const setOpen = open => {
        popover.hidden = !open;
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
    };
    toggle.addEventListener("click", event => {
        event.stopPropagation();
        setOpen(popover.hidden);
    });
    list.addEventListener("click", event => {
        const option = event.target.closest("[data-theme-option]");
        if (!option) return;
        applyTheme(option.dataset.themeOption);
        setOpen(false);
        showToast(`已切换到「${themeById(option.dataset.themeOption).name}」主题`);
    });
    document.addEventListener("click", event => { if (!wrap.contains(event.target)) setOpen(false); });
    document.addEventListener("keydown", event => { if (event.key === "Escape") setOpen(false); });
}

/* ===== 渲染能力分级 =====
   新拟物只依赖 box-shadow，已经没有 backdrop-filter 与 SVG 位移滤镜，
   原先"核显整块背景逐帧重采样"的卡顿根因随之消失。这里保留三档探测，
   用于在极弱设备上收缩阴影半径并关闭过渡：
     perf-high  独立显卡  —— 完整阴影与动效
     perf-mid   集成显卡  —— 完整阴影，关闭过渡动画
     perf-low   软件渲染  —— 阴影半径减半 + 关闭过渡
   优先级：?perf= 查询参数 > 用户手动选择(localStorage) > 自动探测。
   ?perf=high|mid|low 便于在同一台机器上对比三档差异。 */
const PERF_KEY = "curl2py.perf.v1";
const PERF_LEVELS = ["low", "mid", "high"];
const PERF_OPTIONS = [
    { id: "auto", name: "自动（按设备能力）" },
    { id: "high", name: "精细（完整阴影与动效）" },
    { id: "mid", name: "流畅（关闭过渡动画）" },
    { id: "low", name: "极简（阴影减半）" },
];

function storedPerfChoice() {
    try {
        const value = localStorage.getItem(PERF_KEY);
        if (PERF_OPTIONS.some(option => option.id === value)) return value;
    } catch (_error) { /* 隐私模式不记忆 */ }
    return "auto";
}

function detectRenderProfile() {
    const forced = new URLSearchParams(location.search).get("perf");
    if (PERF_LEVELS.includes(forced)) return forced;

    const manual = storedPerfChoice();
    if (manual !== "auto") return manual;

    let renderer = "";
    try {
        const canvas = document.createElement("canvas");
        // 带性能约束取不到上下文，说明浏览器已判定为软件渲染或无 WebGL，直接降级
        const gl = canvas.getContext("webgl", { failIfMajorPerformanceCaveat: true });
        if (!gl) return "low";
        const info = gl.getExtension("WEBGL_debug_renderer_info");
        renderer = String(
            (info && gl.getParameter(info.UNMASKED_RENDERER_WEBGL))
            || gl.getParameter(gl.RENDERER)
            || ""
        );
        const lose = gl.getExtension("WEBGL_lose_context");
        if (lose) lose.loseContext();
    } catch (error) {
        return "low";
    }

    const name = renderer.toLowerCase();
    if (!name || /swiftshader|llvmpipe|softpipe|software|basic render|microsoft basic|mesa offscreen|generic renderer/.test(name)) return "low";
    if (/nvidia|geforce|rtx|gtx|quadro|radeon\s+(rx|pro|vii)|arc\s+a\d|apple\s+m\d/.test(name)) return "high";
    if ((navigator.hardwareConcurrency || 4) <= 2) return "low";
    return "mid";
}

function applyRenderProfile(forced) {
    const profile = PERF_LEVELS.includes(forced) ? forced : detectRenderProfile();
    const root = document.documentElement;
    root.classList.remove("perf-high", "perf-mid", "perf-low");
    root.classList.add("perf-" + profile);
    root.dataset.perf = profile;
}

/* 兜底：探测结果与实际帧率不符时再降一档。
   某些老核显（如 HD 4000）报告的是真实 GPU 型号，却带不动 backdrop-filter，
   仅靠型号无法判断。这里在页面加载后采样 2 秒帧间隔，若 75 分位已慢于 32ms
   （约 30fps 以下）说明当前档位确实撑不住，降一档并记入会话，避免反复重测。 */
function watchFrameHealth() {
    const root = document.documentElement;
    if (root.dataset.perf === "low") return;
    if (storedPerfChoice() !== "auto") return;
    // 查询参数是调试用的强制档位，不该被自动降级覆盖
    if (PERF_LEVELS.includes(new URLSearchParams(location.search).get("perf"))) return;
    try { if (sessionStorage.getItem("curl2py.perf.fallback")) return; } catch (_error) { return; }

    const gaps = [];
    let start = 0;
    let last = 0;
    const tick = now => {
        if (!start) { start = now; last = now; }
        // 页面不可见时 rAF 会被暂停，采样不可信
        if (document.visibilityState === "visible" && now - start > 700) gaps.push(now - last);
        last = now;
        if (now - start < 2600) return requestAnimationFrame(tick);
        if (gaps.length < 30) return;
        gaps.sort((a, b) => a - b);
        const p75 = gaps[Math.floor(gaps.length * 0.75)];
        if (p75 <= 32) return;
        const current = root.dataset.perf;
        const next = PERF_LEVELS[Math.max(PERF_LEVELS.indexOf(current) - 1, 0)];
        if (next === current) return;
        applyRenderProfile(next);
        try { sessionStorage.setItem("curl2py.perf.fallback", next); } catch (_error) { /* 忽略 */ }
    };
    requestAnimationFrame(tick);
}

function initPerfControl() {
    const list = $("#perf-list");
    const popover = $("#theme-popover");
    if (!list || !popover || list.dataset.bound) return;
    list.dataset.bound = "true";
    list.innerHTML = PERF_OPTIONS.map(option =>
        `<button class="theme-option" type="button" role="radio" data-perf-option="${option.id}"><span>${option.name}</span><span class="theme-check">✓</span></button>`
    ).join("");
    const sync = () => {
        const active = storedPerfChoice();
        list.querySelectorAll("[data-perf-option]").forEach(button => {
            const on = button.dataset.perfOption === active;
            button.classList.toggle("active", on);
            button.setAttribute("aria-checked", on ? "true" : "false");
        });
    };
    sync();
    list.addEventListener("click", event => {
        const option = event.target.closest("[data-perf-option]");
        if (!option) return;
        const choice = option.dataset.perfOption;
        try { localStorage.setItem(PERF_KEY, choice); } catch (_error) { /* 忽略 */ }
        try { sessionStorage.removeItem("curl2py.perf.fallback"); } catch (_error) { /* 忽略 */ }
        applyRenderProfile(choice === "auto" ? undefined : choice);
        sync();
        popover.hidden = true;
        showToast(`渲染效果：${PERF_OPTIONS.find(item => item.id === choice).name}`);
    });
}

let routeGeneration = 0;
function route() {
    const hash = location.hash || "#/home";
    const riskModal = $("#python-risk-modal");
    if (riskModal && !riskModal.hidden) {
        riskModal.hidden = true;
        document.removeEventListener("keydown", riskModal.__riskKeydown);
    }
    routeGeneration += 1;
    if (hash === "#/home") return renderHome();
    if (hash === "#/curl/python") return renderCurl("python");
    if (hash === "#/curl/feapder") return renderCurl("feapder");
    if (hash === "#/json/diff") return renderJsonDiff();
    if (hash === "#/text/diff") return renderTextDiff();
    if (hash === "#/text/html") return renderHtml();
    if (hash === "#/time") return renderTime();
    if (hash === "#/crypto/base64") return renderCrypto("base64");
    if (hash === "#/crypto/hash") return renderCrypto("hash");
    if (hash === "#/crypto") return renderCryptoWorkbench();
    if (hash === "#/base64/image") return renderImageBase64();
    if (hash === "#/vault") return renderVault();
    if (hash.startsWith("#/format/")) return renderFormatter(hash.split("/")[2]);
    if (hash.startsWith("#/url/")) return renderUrl(hash.split("/")[2]);
    renderHome();
}

window.addEventListener("hashchange", route);
applyRenderProfile();
initTheme();
initPerfControl();
bindSearch();
route();
watchFrameHealth();
loadCatalog().then(route);
