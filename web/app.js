import { state } from "./src/state.js?v=20260704-report-action-ui";
import { configureApi, api } from "./src/api.js?v=20260704-report-action-ui";
import { $, escapeHTML, fmt, shortText, statusClass } from "./src/utils.js?v=20260704-report-action-ui";
import {
  isTerminalRun,
  isTerminalVideoJob,
  renderAuditLogsTable,
  renderAuthUsersTable,
  renderLLMCallLogsTable,
  renderLLMCallStats,
  renderMaintenance,
  renderReportsTable,
  renderRunEvents,
  renderRunsTable,
  renderTTSVoicesTable,
  renderVideosTable,
  reportActions,
  reportVideoLive,
  ttsActions,
} from "./src/renderers.js?v=20260704-report-action-ui";

function setOutput(message) {
  $("#overview-output").textContent =
    typeof message === "string" ? message : JSON.stringify(message, null, 2);
}

// 控制台岛通信桥（由 src/islands.js 在本模块之前初始化到 window.glassBridge）。
// 若 islands.js 加载失败，降级为空操作，避免拖垮控制台其余功能。
const glassBridge =
  window.glassBridge || {
    set() {},
    get: () => ({}),
    subscribe() {
      return () => {};
    },
    on() {
      return () => {};
    },
    emit() {},
  };

let toastSeq = 0;
const toastTimers = new Map();

function dismissToast(id) {
  const timer = toastTimers.get(id);
  if (timer) {
    clearTimeout(timer);
    toastTimers.delete(id);
  }
  const cur = glassBridge.get("toast").toasts || [];
  glassBridge.set("toast", { toasts: cur.filter((item) => item.id !== id) });
}

function showToast(message, type = "info", timeout = 3200) {
  const id = ++toastSeq;
  const msg = typeof message === "string" ? message : JSON.stringify(message);
  const cur = glassBridge.get("toast").toasts || [];
  glassBridge.set("toast", { toasts: cur.concat([{ id, msg, type }]) });
  if (timeout > 0) {
    toastTimers.set(id, setTimeout(() => dismissToast(id), timeout));
  }
  return () => dismissToast(id);
}

function setBusy(element, busy) {
  if (!element) {
    return;
  }
  element.toggleAttribute("disabled", busy);
  element.classList.toggle("is-busy", busy);
}

function showLanding() {
  closeRunEventStream();
  closeVideoJobStream();
  $("#app-shell").classList.add("hidden");
  $("#view-landing").classList.remove("hidden");
  // 让登录表单容器可见（mount 岛的 data-glass="login" 在 landing 区）
  const loginHost = document.querySelector('[data-glass="login"]');
  if (loginHost) {
    loginHost.classList.remove("hidden");
  }
  glassBridge.set("topbar", {
    authText: "未登录",
    authStatus: "fail",
    logoutVisible: false,
  });
}

function hideLanding() {
  $("#app-shell").classList.remove("hidden");
  $("#view-landing").classList.add("hidden");
  const loginHost = document.querySelector('[data-glass="login"]');
  if (loginHost) {
    loginHost.classList.add("hidden");
  }
  glassBridge.set("topbar", {
    authText: state.auth.auth_enabled
      ? `已登录：${state.auth.username || "admin"} / ${state.auth.role || "viewer"}`
      : "未启用登录",
    authStatus: "ok",
    logoutVisible: state.auth.auth_enabled,
  });
  applyAuthVisibility();
}

function showLogin() {
  showLanding();
}

function applyAuthVisibility() {
  const isAdmin = !state.auth.auth_enabled || state.auth.role === "admin";
  document.querySelectorAll("[data-requires-admin]").forEach((item) => {
    item.classList.toggle("hidden", !isAdmin);
  });
  const activeTab = document.querySelector(".tab.active");
  if (activeTab?.dataset.requiresAdmin && !isAdmin) {
    document.querySelector('[data-tab="overview"]').click();
  }
}

function hideLogin() {
  hideLanding();
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      $(`#view-${button.dataset.tab}`).classList.add("active");
      if (window.location.hash !== `#${button.dataset.tab}`) {
        window.history.replaceState(null, "", `#${button.dataset.tab}`);
      }
    });
  });
}

function restoreActiveTab() {
  const tabName = window.location.hash.replace("#", "");
  if (!tabName) {
    return;
  }
  const button = Array.from(document.querySelectorAll(".tab")).find(
    (item) => item.dataset.tab === tabName,
  );
  if (button && !button.classList.contains("hidden")) {
    button.click();
  }
}

async function refreshAuth() {
  const data = await api("/auth/me");
  state.auth = data;
  if (data.auth_enabled && !data.authenticated) {
    applyAuthVisibility();
    showLogin();
    return false;
  }
  hideLogin();
  return true;
}

async function refreshHealth() {
  try {
    await api("/health");
    glassBridge.set("topbar", { healthText: "服务正常", healthStatus: "ok" });
    updateLandingHealth("服务正常", "ok");
  } catch (error) {
    glassBridge.set("topbar", { healthText: "服务异常", healthStatus: "fail" });
    updateLandingHealth("服务异常", "fail");
  }
}

function updateLandingHealth(text, status) {
  const badge = $("#landing-health-badge");
  const textEl = $("#landing-health-text");
  if (!badge || !textEl) return;
  badge.className = `topbar-badge ${status}`;
  textEl.textContent = text;
}

async function refreshRSS() {
  const data = await api("/rss/sources");
  state.rss = data.sources || [];
  $("#metric-rss").textContent = state.rss.filter((item) => Boolean(item.enabled)).length;
  $("#rss-table").innerHTML = state.rss
    .map(
      (item) => `
        <tr>
          <td>${item.id}</td>
          <td>${fmt(item.name)}</td>
          <td>${fmt(item.category) || "<span class='muted'>未分类</span>"}</td>
          <td class="muted">${fmt(item.url)}</td>
          <td><span class="${statusClass(item.enabled ? "ok" : "failed")}">${item.enabled ? "启用" : "停用"}</span></td>
          <td>${fmt(item.last_success_at)}</td>
          <td>${fmt(item.consecutive_failures)}</td>
          <td>
            <button class="link-button" data-edit-rss-source="${item.id}" type="button" aria-label="编辑 RSS 源 ${item.id}">编辑</button>
            <button class="link-button" data-test-rss-source="${item.id}" type="button" aria-label="测试 RSS 源 ${item.id}">测试</button>
            <button class="link-button" data-preview-rss-source="${item.id}" type="button" aria-label="预览 RSS 源 ${item.id}">预览</button>
            <button class="link-button" data-collect-source="${item.id}" type="button" aria-label="采集 RSS 源 ${item.id}">采集</button>
            ${
              item.enabled
                ? `<button class="link-button" data-disable-rss-source="${item.id}" type="button" aria-label="停用 RSS 源 ${item.id}">停用</button>`
                : ""
            }
            <button class="link-button danger-link" data-delete-rss-source="${item.id}" type="button" aria-label="删除 RSS 源 ${item.id}">删除</button>
          </td>
        </tr>
      `,
    )
    .join("");
}

async function refreshModels() {
  const data = await api("/model-configs");
  state.models = data.model_configs || [];
  $("#models-table").innerHTML = state.models
    .map(
      (item) => {
        const typeLabel = item.model_type === "tts" ? "TTS" : "LLM";
        const typeBadge = `<span class="badge-${item.model_type || "llm"}">${typeLabel}</span>`;
        const modelOrVoice = item.model_type === "tts"
          ? `${fmt(item.model_name || "—")} / 🎙 ${fmt(item.voice || "—")}`
          : fmt(item.model_name || "—");
        return `
        <tr>
          <td>${item.id}</td>
          <td>${typeBadge}</td>
          <td>${fmt(item.name)}</td>
          <td class="muted">${fmt(item.base_url)}</td>
          <td>${modelOrVoice}</td>
          <td>${fmt(item.api_key_masked)}</td>
          <td><span class="${statusClass(item.enabled ? "ok" : "failed")}">${item.enabled ? "启用" : "停用"}</span></td>
          <td>${item.is_default ? "是" : "否"}</td>
          <td>
            <button class="link-button" data-edit-model="${item.id}" type="button" aria-label="编辑模型配置 ${item.id}">编辑</button>
            <button class="link-button" data-test-model="${item.id}" type="button" aria-label="测试模型配置 ${item.id}">测试</button>
            <button class="link-button" data-delete-model="${item.id}" type="button" aria-label="删除模型配置 ${item.id}">删除</button>
          </td>
        </tr>
      `;
      },
    )
    .join("");
  renderTTSVoicesTable();
}

async function refreshTTSVoices() {
  try {
    const query = ttsAudioFilterQuery();
    const data = await api(`/tts-audio-assets${query ? `?${query}` : ""}`);
    state.ttsAudioAssets = data.tts_audio_assets || [];
    renderTTSVoicesTable();
  } catch (error) {
    state.ttsAudioAssets = [];
    renderTTSVoicesTable();
    showToast(error.message || "语音管理数据加载失败。", "fail", 8000);
  }
}

function ttsAudioFilterQuery() {
  const form = document.getElementById("tts-audio-filter-form");
  if (!form) return "";
  const payload = formJSON(form);
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(payload)) {
    if (value === "" || value === false || value === null || value === undefined) {
      continue;
    }
    params.set(key, value);
  }
  return params.toString();
}

async function refreshPrompts() {
  const data = await api("/prompt-versions");
  state.prompts = data.prompt_versions || [];
  $("#prompts-table").innerHTML = state.prompts
    .map(
      (item) => `
        <tr>
          <td>${item.id}</td>
          <td>
            <strong>${escapeHTML(item.name)}</strong>
            <div class="muted">${escapeHTML(shortText(item.system_prompt, 72))}</div>
          </td>
          <td><span class="${statusClass(item.enabled ? "ok" : "failed")}">${item.enabled ? "启用" : "停用"}</span></td>
          <td>${item.is_default ? "是" : "否"}</td>
          <td>${fmt(item.updated_at)}</td>
          <td>
            <button class="link-button" data-edit-prompt="${item.id}" type="button" aria-label="编辑提示词 ${item.id}">编辑</button>
            <button class="link-button" data-delete-prompt="${item.id}" type="button" aria-label="删除提示词 ${item.id}">删除</button>
          </td>
        </tr>
      `,
    )
    .join("");
}

async function refreshSchedules() {
  const data = await api("/schedules");
  state.schedules = data.schedules || [];
  $("#metric-schedules").textContent = state.schedules.length;
  populateScheduleFormDefaults();
  const modelName = (id) => {
    if (!id) return "默认";
    const m = (state.models || []).find((x) => String(x.id) === String(id));
    return m ? m.name : `#${id}`;
  };
  const promptName = (id) => {
    if (!id) return "默认";
    const p = (state.prompts || []).find((x) => String(x.id) === String(id));
    return p ? p.name : `#${id}`;
  };
  const ttsName = (id) => {
    if (!id) return "默认";
    const m = (state.models || []).find((x) => String(x.id) === String(id));
    return m ? m.name : `#${id}`;
  };
  const renderEngineName = (value) => (value === "ffmpeg" ? "FFmpeg 模板" : "Remotion");
  $("#schedule-table").innerHTML = state.schedules
    .map(
      (item) => `
        <tr>
          <td>${item.id}</td>
          <td>${fmt(item.name)}</td>
          <td>${fmt(item.task_type)}</td>
          <td>${fmt(item.cron_expression)}</td>
          <td>${fmt(item.rss_category) || "<span class='muted'>全部</span>"}</td>
          <td>${escapeHTML(modelName(item.model_config_id))}</td>
          <td>${escapeHTML(promptName(item.prompt_version_id))}</td>
          <td>${escapeHTML(ttsName(item.tts_config_id))}</td>
          <td>${escapeHTML(renderEngineName(item.render_engine))}</td>
          <td>${fmt(item.report_type)}</td>
          <td>
            <span class="${statusClass(item.auto_render ? "ok" : "failed")}">${item.auto_render ? "渲染" : "不渲染"}</span>
            /
            <span class="${statusClass(item.auto_publish ? "ok" : "failed")}">${item.auto_publish ? "发布" : "不发布"}</span>
          </td>
          <td><span class="${statusClass(item.enabled ? "ok" : "failed")}">${item.enabled ? "启用" : "停用"}</span></td>
          <td>${fmt(item.next_run_at)}</td>
          <td>
            <button class="link-button" data-run-schedule="${item.id}" type="button" aria-label="执行定时计划 ${item.id}">执行</button>
            <button class="link-button" data-disable-schedule="${item.id}" type="button" aria-label="${item.enabled ? "禁用" : "启用"}定时计划 ${item.id}">${item.enabled ? "禁用" : "启用"}</button>
            <button class="link-button" data-edit-schedule="${item.id}" type="button" aria-label="编辑定时计划 ${item.id}">编辑</button>
          </td>
        </tr>
      `,
    )
    .join("");
}

function populateScheduleFormDefaults() {
  const form = $("#schedule-form");
  if (!form) return;
  // 模型下拉（仅 LLM 类型）
  const modelSel = form.elements.model_config_id;
  if (modelSel) {
    const cur = modelSel.value;
    const llmModels = (state.models || []).filter((m) => m.model_type !== "tts");
    modelSel.innerHTML =
      '<option value="">默认</option>' +
      llmModels
        .map((m) => `<option value="${m.id}">${escapeHTML(m.name)}</option>`)
        .join("");
    modelSel.value = cur;
  }
  // 提示词下拉
  const promptSel = form.elements.prompt_version_id;
  if (promptSel) {
    const cur = promptSel.value;
    promptSel.innerHTML =
      '<option value="">默认</option>' +
      (state.prompts || [])
        .map((p) => `<option value="${p.id}">${escapeHTML(p.name)}</option>`)
        .join("");
    promptSel.value = cur;
  }
  // TTS 配置下拉（仅 TTS 类型）
  const ttsSel = form.elements.tts_config_id;
  if (ttsSel) {
    const cur = ttsSel.value;
    const ttsModels = (state.models || []).filter((m) => m.model_type === "tts");
    ttsSel.innerHTML =
      '<option value="">默认</option>' +
      ttsModels
        .map((m) => `<option value="${m.id}">${escapeHTML(m.name)}</option>`)
        .join("");
    ttsSel.value = cur;
  }
  // RSS 分类候选
  const dl = document.getElementById("rss-category-options");
  if (dl) {
    dl.innerHTML = (state.rss || [])
      .map((s) => s.category)
      .filter((c, i, arr) => c && arr.indexOf(c) === i)
      .map((c) => `<option value="${escapeHTML(c)}"></option>`)
      .join("");
  }
}

function populateReportFilterDefaults() {
  const form = $("#report-filter-form");
  if (!form) return;

  // Report types - extract unique from loaded reports
  const reportTypes = [...new Set((state.reports || []).map((r) => r.report_type).filter(Boolean))];
  const curType = form.elements.report_type.value;
  form.elements.report_type.innerHTML =
    '<option value="">全部</option>' +
    reportTypes.map((t) => `<option value="${escapeHTML(t)}">${escapeHTML(t)}</option>`).join("");
  form.elements.report_type.value = curType;

  // RSS categories - extract unique from loaded RSS sources
  const categories = [
    ...new Set((state.rss || []).map((s) => s.category).filter(Boolean)),
  ];
  const curCat = form.elements.rss_category.value;
  form.elements.rss_category.innerHTML =
    '<option value="">全部</option>' +
    categories.map((c) => `<option value="${escapeHTML(c)}">${escapeHTML(c)}</option>`).join("");
  form.elements.rss_category.value = curCat;

  // Models
  const curModel = form.elements.model_config_id.value;
  form.elements.model_config_id.innerHTML =
    '<option value="">全部</option>' +
    (state.models || [])
      .map((m) => `<option value="${m.id}">${escapeHTML(m.name)}</option>`)
      .join("");
  form.elements.model_config_id.value = curModel;

  // Prompts
  const curPrompt = form.elements.prompt_version_id.value;
  form.elements.prompt_version_id.innerHTML =
    '<option value="">全部</option>' +
    (state.prompts || [])
      .map((p) => `<option value="${p.id}">${escapeHTML(p.name)}</option>`)
      .join("");
  form.elements.prompt_version_id.value = curPrompt;
}

function populateOverviewFormDefaults() {
  const form = $("#overview-run-form");
  if (!form) return;

  // Model dropdown (LLM only)
  const modelSel = form.elements.model_config_id;
  if (modelSel) {
    const cur = modelSel.value;
    const llmModels = (state.models || []).filter((m) => m.model_type !== "tts");
    modelSel.innerHTML =
      '<option value="">默认</option>' +
      llmModels
        .map((m) => `<option value="${m.id}">${escapeHTML(m.name)}</option>`)
        .join("");
    modelSel.value = cur;
  }

  // Prompt dropdown
  const promptSel = form.elements.prompt_version_id;
  if (promptSel) {
    const cur = promptSel.value;
    promptSel.innerHTML =
      '<option value="">默认</option>' +
      (state.prompts || [])
        .map((p) => `<option value="${p.id}">${escapeHTML(p.name)}</option>`)
        .join("");
    promptSel.value = cur;
  }

  // TTS dropdown (TTS type only)
  const ttsSel = form.elements.tts_config_id;
  if (ttsSel) {
    const cur = ttsSel.value;
    const ttsModels = (state.models || []).filter((m) => m.model_type === "tts");
    ttsSel.innerHTML =
      '<option value="">默认</option>' +
      ttsModels
        .map((m) => `<option value="${m.id}">${escapeHTML(m.name)}</option>`)
        .join("");
    ttsSel.value = cur;
  }
}

function reportFilterQuery() {
  const form = $("#report-filter-form");
  if (!form) return "";
  const payload = formJSON(form);
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(payload)) {
    if (value === "" || value === false || value === null || value === undefined) {
      continue;
    }
    params.set(key, value);
  }
  return params.toString();
}

async function refreshReports() {
  const query = reportFilterQuery();
  const data = await api(`/reports${query ? `?${query}` : ""}`);
  state.reports = data.reports || [];
  renderReportsTable();
}

function videoFilterQuery() {
  const payload = formJSON($("#video-filter-form"));
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(payload)) {
    if (value === "" || value === false || value === null || value === undefined) {
      continue;
    }
    params.set(key, value);
  }
  return params.toString();
}

async function refreshVideos() {
  const query = videoFilterQuery();
  const data = await api(`/video-jobs${query ? `?${query}` : ""}`);
  state.videoJobs = data.video_jobs || [];
  renderVideosTable();
}

async function refreshRuns() {
  const data = await api("/runs");
  state.runs = data.runs || [];
  renderRunsTable();
}

async function refreshLLMCallLogs() {
  const filter = $("#llm-log-run-filter").value;
  const query = filter ? `?pipeline_run_id=${encodeURIComponent(filter)}&limit=100` : "?limit=100";
  const data = await api(`/llm-call-logs${query}`);
  state.llmCallLogs = data.items || [];
  renderLLMCallLogsTable();
}

async function refreshLLMCallStats() {
  const days = $("#llm-stats-days").value || "7";
  const data = await api(`/llm-call-stats?days=${encodeURIComponent(days)}`);
  state.llmCallStats = data;
  renderLLMCallStats();
}

async function refreshLLMPanel() {
  await Promise.all([refreshLLMCallStats(), refreshLLMCallLogs()]);
}

async function refreshSecurityPanel() {
  if (state.auth.auth_enabled && state.auth.role !== "admin") {
    state.authUsers = [];
    state.auditLogs = [];
    renderAuthUsersTable();
    renderAuditLogsTable();
    return;
  }
  const [users, logs] = await Promise.all([
    api("/auth/users"),
    api("/auth/audit-logs?limit=100"),
  ]);
  state.authUsers = users.items || [];
  state.auditLogs = logs.items || [];
  renderAuthUsersTable();
  renderAuditLogsTable();
}

async function refreshMaintenance() {
  const data = await api("/maintenance/summary");
  state.maintenance = data;
  renderMaintenance();
}

async function refreshAll() {
  await refreshHealth();
  await Promise.all([
    refreshRSS(),
    refreshModels(),
    refreshTTSVoices(),
    refreshPrompts(),
    refreshSchedules(),
    refreshReports(),
    refreshVideos(),
    refreshRuns(),
    refreshLLMPanel(),
    refreshMaintenance(),
    refreshSecurityPanel(),
  ]);
  populateReportFilterDefaults();
  populateOverviewFormDefaults();
}

function formJSON(form) {
  const data = new FormData(form);
  const payload = {};
  for (const [key, value] of data.entries()) {
    payload[key] = value;
  }
  form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    payload[input.name] = input.checked;
  });
  return payload;
}

function updateModelFormVisibility() {
  const form = document.getElementById("model-form");
  if (!form) return;
  const select = form.elements["model_type"];
  const type = select ? select.value : "llm";
  form.setAttribute("data-model-type", type);
  const modelName = form.elements["model_name"];
  const voice = form.elements["voice"];
  if (type === "tts") {
    if (modelName) {
      modelName.required = true;
      modelName.placeholder = "qwen3-tts / qwen3-tts-1.7b";
    }
    if (voice) voice.required = true;
  } else {
    if (modelName) {
      modelName.required = true;
      modelName.placeholder = "deepseek-chat / gpt-4o-mini";
    }
    if (voice) voice.required = false;
  }
}

function resetModelForm() {
  const form = document.getElementById("model-form");
  if (!form) return;
  form.reset();
  form.elements["id"].value = "";
  form.querySelector('button[type="submit"]').textContent = "保存模型";
  const cancelBtn = document.getElementById("btn-cancel-model-edit");
  if (cancelBtn) cancelBtn.classList.add("hidden");
  updateModelFormVisibility();
}

function resetRSSForm() {
  const form = document.getElementById("rss-form");
  if (!form) return;
  form.reset();
  form.elements["id"].value = "";
  form.elements["priority"].value = "100";
  form.elements["request_timeout_seconds"].value = "20";
  form.elements["enabled"].checked = true;
  form.querySelector('button[type="submit"]').textContent = "保存 RSS 源";
  const cancelBtn = document.getElementById("btn-cancel-rss-edit");
  if (cancelBtn) cancelBtn.classList.add("hidden");
}

function resetPromptForm() {
  const form = document.getElementById("prompt-form");
  if (!form) return;
  form.reset();
  form.elements["id"].value = "";
  form.elements["enabled"].checked = true;
  form.elements["is_default"].checked = false;
  form.querySelector('button[type="submit"]').textContent = "保存提示词";
}

function resetScheduleForm() {
  const form = document.getElementById("schedule-form");
  if (!form) return;
  form.reset();
  form.elements["id"].value = "";
  populateScheduleFormDefaults();
  form.elements["enabled"].checked = true;
  form.elements["prevent_overlap"].checked = true;
  form.elements["auto_render"].checked = true;
  form.elements["auto_publish"].checked = false;
  form.elements["render_engine"].value = "remotion";
  form.querySelector('button[type="submit"]').textContent = "保存计划";
}

// ── 弹窗管理 ──
const _dialogs = {};

function registerDialog(id, opts) {
  const dialog = document.getElementById(id);
  if (!dialog) return;
  _dialogs[id] = { dialog, ...opts };
  dialog.querySelector(".dialog-close")?.addEventListener("click", () => closeDialog(id));
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) closeDialog(id);
  });
}

function openDialog(id, opts = {}) {
  const reg = _dialogs[id];
  if (!reg) return;
  const titleEl = reg.dialog.querySelector(".dialog-head h3");
  if (titleEl && opts.title) titleEl.textContent = opts.title;
  if (opts.populate) {
    opts.populate();
  } else if (reg.resetForm) {
    reg.resetForm();
  }
  reg.dialog.showModal();
}

function closeDialog(id) {
  const reg = _dialogs[id];
  if (!reg) return;
  reg.dialog.close();
  if (reg.resetForm) reg.resetForm();
}

function bindDialogs() {
  registerDialog("rss-dialog", { resetForm: resetRSSForm });
  registerDialog("model-dialog", { resetForm: resetModelForm });
  registerDialog("prompt-dialog", { resetForm: resetPromptForm });
  registerDialog("schedule-dialog", { resetForm: resetScheduleForm });

  document.getElementById("btn-add-rss")?.addEventListener("click", () => {
    openDialog("rss-dialog", { title: "增加 RSS 源" });
  });
  document.getElementById("btn-add-model")?.addEventListener("click", () => {
    openDialog("model-dialog", { title: "增加模型配置" });
  });
  document.getElementById("btn-add-prompt")?.addEventListener("click", () => {
    openDialog("prompt-dialog", { title: "增加提示词版本" });
  });
  document.getElementById("btn-add-schedule")?.addEventListener("click", () => {
    openDialog("schedule-dialog", { title: "增加定时计划" });
  });
}

function bindForms() {
  glassBridge.on("submit:login", async (payload) => {
    glassBridge.set("login", { error: "" });
    try {
      const data = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.auth = data;
      glassBridge.set("login", { error: "" });
      hideLanding();
      await refreshAll();
      setOutput("登录成功。");
      document.querySelector('[data-tab="overview"]').click();
      showToast(`登录成功，欢迎 ${data.username || "admin"}`, "ok");
    } catch (loginError) {
      const msg = loginError.message || "登录失败";
      // 错误已通过 glassBridge.set("login", { error }) 显示在表单内，持久可复制
      glassBridge.set("login", { error: msg });
    }
  });

  $("#auth-user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJSON(form);
    await api("/auth/users", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    form.reset();
    await refreshSecurityPanel();
    setOutput("用户已创建。");
  });

  $("#auth-password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJSON(form);
    const confirmed = window.confirm(`确认重置用户 #${payload.user_id} 的密码？此操作不可撤销。`);
    if (!confirmed) {
      return;
    }
    const userId = payload.user_id;
    delete payload.user_id;
    await api(`/auth/users/${userId}/password`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    form.reset();
    await refreshSecurityPanel();
    setOutput("密码已重置。");
  });

  $("#rss-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJSON(form);
    payload.priority = Number(payload.priority || 100);
    payload.request_timeout_seconds = Number(payload.request_timeout_seconds || 20);
    payload.language = "zh-CN";
    if (!payload.id) {
      delete payload.id;
    } else {
      payload.id = Number(payload.id);
    }
    await api("/rss/sources", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    resetRSSForm();
    await refreshRSS();
    showToast(payload.id ? "RSS 源已更新。" : "RSS 源已保存。", "ok");
    closeDialog("rss-dialog");
  });

  $("#schedule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJSON(form);
    payload.max_runtime_seconds = Number(payload.max_runtime_seconds || 3600);
    payload.retry_count = Number(payload.retry_count || 0);
    payload.retry_interval_seconds = Number(payload.retry_interval_seconds || 300);
    // 数值字段：空串视为未指定（null）
    for (const key of ["model_config_id", "prompt_version_id", "tts_config_id"]) {
      payload[key] = payload[key] ? Number(payload[key]) : null;
    }
    if (!payload.rss_category) {
      payload.rss_category = null;
    }
    if (!payload.id) {
      delete payload.id;
    }
    await api("/schedules", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    resetScheduleForm();
    await refreshSchedules();
    showToast(payload.id ? "定时计划已更新。" : "定时计划已保存。", "ok");
    closeDialog("schedule-dialog");
  });

  const cancelModelBtn = $("#btn-cancel-model-edit");
  if (cancelModelBtn) {
    cancelModelBtn.addEventListener("click", () => {
      resetModelForm();
      showToast("已取消编辑", "info");
    });
  }

  const cancelRSSBtn = $("#btn-cancel-rss-edit");
  if (cancelRSSBtn) {
    cancelRSSBtn.addEventListener("click", () => {
      resetRSSForm();
      showToast("已取消编辑 RSS 源", "info");
    });
  }

  const modelTypeSelect = $("#model-type-select");
  if (modelTypeSelect) {
    modelTypeSelect.addEventListener("change", updateModelFormVisibility);
    updateModelFormVisibility();
  }

  $("#model-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJSON(form);
    const modelType = payload.model_type || "llm";
    const isEditing = Boolean(payload.id);

    if (isEditing) {
      payload.id = Number(payload.id);
    } else {
      delete payload.id;
    }

    if (modelType === "tts") {
      // TTS: model_name and voice are both required by most TTS APIs.
      if (!payload.model_name) {
        showToast("TTS 配置必须填写模型名称。", "fail");
        return;
      }
      if (!payload.voice) {
        showToast("TTS 配置必须填写音色（Voice）。", "fail");
        return;
      }
      payload.provider =
        payload.base_url.includes("compatible-mode") || payload.base_url.endsWith("/audio/speech")
          ? "openai-compatible-tts"
          : "tts-service";
      payload.temperature = 0;
      payload.timeout_seconds = 600;
      payload.max_retries = 0;
    } else {
      // LLM: model_name required
      if (!payload.model_name) {
        showToast("LLM 配置必须填写模型名称。", "fail");
        return;
      }
      payload.provider = "openai-compatible";
      payload.timeout_seconds = Number(payload.timeout_seconds || 180);
      payload.temperature = Number(payload.temperature || 0.2);
      payload.max_retries = 2;
      payload.voice = null;
    }

    if (!payload.api_key) {
      delete payload.api_key;
    }
    const submitBtn = form.querySelector('button[type="submit"]');
    setBusy(submitBtn, true);
    try {
      await api("/model-configs", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      resetModelForm();
      await refreshModels();
      await refreshTTSVoices();
      showToast(isEditing ? "模型配置已更新。" : "模型配置已保存。", "ok");
      closeDialog("model-dialog");
    } catch (error) {
      showToast(error.message || "保存失败，请重试。", "fail");
    } finally {
      setBusy(submitBtn, false);
    }
  });

  $("#prompt-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJSON(form);
    const isEditing = Boolean(payload.id);
    if (payload.id) {
      payload.id = Number(payload.id);
    } else {
      delete payload.id;
    }
    await api("/prompt-versions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    resetPromptForm();
    await refreshPrompts();
    showToast(isEditing ? "提示词已更新。" : "提示词已保存。", "ok");
    closeDialog("prompt-dialog");
  });

  $("#news-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.selectedReportId) {
      setOutput("请先选择报告。");
      return;
    }
    const form = event.currentTarget;
    const payload = formJSON(form);
    const newsId = payload.id;
    delete payload.id;
    payload.item_index = Number(payload.item_index || 1);
    await api(`/reports/${state.selectedReportId}/news/${newsId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    hideNewsEditForm();
    await refreshReports();
    await loadReportDetail(state.selectedReportId);
    setOutput("新闻已保存。重新渲染视频后会使用更新后的内容。");
  });

  $("#video-filter-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await refreshVideos();
    setOutput("视频资产筛选已刷新。");
  });

  document.getElementById("tts-audio-filter-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await refreshTTSVoices();
    setOutput("语音资产筛选已刷新。");
  });

  $("#report-filter-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await refreshReports();
    setOutput("报告筛选已刷新。");
  });

  $("#overview-run-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJSON(form);
    payload.hours = Number(payload.hours || 24);
    payload.limit = Number(payload.limit || 120);
    for (const key of ["model_config_id", "prompt_version_id", "tts_config_id"]) {
      payload[key] = payload[key] ? Number(payload[key]) : null;
    }
    if (!payload.rss_category) {
      payload.rss_category = null;
    }
    if (!payload.report_type) {
      payload.report_type = "general";
    }
    const result = await api("/pipeline/run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await refreshRuns();
    setOutput(result);
    showToast(`任务已提交：run_id=${result.run_id}`, "ok");
  });

  $("#maintenance-cleanup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJSON(form);
    payload.output_retention_days = Number(payload.output_retention_days || 30);
    payload.audio_retention_days = Number(payload.audio_retention_days || 14);
    payload.cache_retention_days = Number(payload.cache_retention_days || 7);
    if (!payload.dry_run) {
      const confirmed = window.confirm("确认清理候选文件？这个操作会删除旧输出、旧音频和旧缓存文件。");
      if (!confirmed) {
        return;
      }
    }
    const result = await api("/maintenance/cleanup", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#maintenance-output").textContent = JSON.stringify(result, null, 2);
    await refreshMaintenance();
    setOutput(result);
  });
}

function bindActions() {
  $("#btn-refresh-rss").addEventListener("click", refreshRSS);
  $("#btn-refresh-models").addEventListener("click", refreshModels);
  document.getElementById("btn-refresh-tts-voices")?.addEventListener("click", refreshTTSVoices);
  $("#btn-refresh-prompts").addEventListener("click", refreshPrompts);
  $("#btn-refresh-schedules").addEventListener("click", refreshSchedules);
  $("#btn-refresh-reports").addEventListener("click", refreshReports);
  $("#btn-refresh-videos").addEventListener("click", refreshVideos);
  $("#btn-refresh-runs").addEventListener("click", refreshRuns);
  $("#btn-refresh-llm-logs").addEventListener("click", refreshLLMPanel);
  $("#btn-refresh-maintenance").addEventListener("click", refreshMaintenance);
  $("#btn-refresh-security").addEventListener("click", refreshSecurityPanel);
  $("#btn-refresh-audit").addEventListener("click", refreshSecurityPanel);
  $("#llm-stats-days").addEventListener("change", refreshLLMCallStats);
  $("#btn-clear-llm-log-filter").addEventListener("click", async () => {
    $("#llm-log-run-filter").value = "";
    await refreshLLMCallLogs();
  });

  $("#btn-cancel-news-edit").addEventListener("click", hideNewsEditForm);

  glassBridge.on("logout", async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {
      // 后端登出接口报错不影响前端状态清理
    }
    closeRunEventStream();
    closeVideoJobStream();
    state.auth = {
      ...state.auth,
      authenticated: false,
      username: null,
      role: null,
    };
    showLanding();
    showToast("已退出登录", "info");
  });

  glassBridge.on("dismiss:toast", ({ id }) => dismissToast(id));

  $("#btn-collect").addEventListener("click", async () => {
    const result = await api("/rss/collect", { method: "POST" });
    await refreshRSS();
    setOutput(result);
  });

  $("#btn-analyze").addEventListener("click", async () => {
    const result = await api("/pipeline/collect-and-analyze", {
      method: "POST",
      body: JSON.stringify({ hours: 24, limit: 120 }),
    });
    await refreshRuns();
    setOutput(result);
  });

  $("#btn-render-latest").addEventListener("click", async () => {
    const result = await api("/render/latest", { method: "POST" });
    await refreshRuns();
    await refreshVideos();
    setOutput(result);
  });

  document.body.addEventListener("keydown", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const reportRow = target.closest("tr[data-report-row]");
    if (!reportRow || !["Enter", " "].includes(event.key)) {
      return;
    }
    event.preventDefault();
    try {
      await loadReportDetail(reportRow.dataset.reportRow);
    } catch (error) {
      console.error("[report-row-keydown] error:", error);
      showToast(error.message || "加载报告详情失败", "fail", 8000);
    }
  });

  document.body.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.matches("button, .button-link, .link-button")) {
      setBusy(target, true);
    }
    try {

    const saveUserId = target.dataset.saveUser;
    if (saveUserId) {
      const user = state.authUsers.find((item) => String(item.id) === String(saveUserId));
      const role = document.querySelector(`[data-user-role="${saveUserId}"]`)?.value || user?.role || "viewer";
      await api(`/auth/users/${saveUserId}`, {
        method: "PUT",
        body: JSON.stringify({ role, enabled: Boolean(Number(user?.enabled ?? 1)) }),
      });
      await refreshSecurityPanel();
      setOutput("用户角色已保存。");
      return;
    }

    const toggleUserId = target.dataset.toggleUser;
    if (toggleUserId) {
      const user = state.authUsers.find((item) => String(item.id) === String(toggleUserId));
      if (!user) {
        return;
      }
      const nextEnabled = Boolean(Number(target.dataset.enabled || 0));
      await api(`/auth/users/${toggleUserId}`, {
        method: "PUT",
        body: JSON.stringify({ role: user.role, enabled: nextEnabled }),
      });
      await refreshSecurityPanel();
      setOutput(nextEnabled ? "用户已启用。" : "用户已停用。");
      return;
    }

    const collectSourceId = target.dataset.collectSource;
    if (collectSourceId) {
      const result = await api(`/rss/collect/${collectSourceId}`, { method: "POST" });
      await refreshRSS();
      setOutput(result);
      return;
    }

    const editRssSourceId = target.dataset.editRssSource;
    if (editRssSourceId) {
      const source = state.rss.find((item) => String(item.id) === String(editRssSourceId));
      if (!source) {
        showToast("没有找到 RSS 源，请刷新后重试。", "fail");
        return;
      }
      openDialog("rss-dialog", {
        title: `编辑 RSS 源：${source.name}`,
        populate: () => {
          const form = document.getElementById("rss-form");
          form.elements["id"].value = source.id || "";
          form.elements["name"].value = source.name || "";
          form.elements["url"].value = source.url || "";
          form.elements["category"].value = source.category || "";
          form.elements["priority"].value = source.priority ?? 100;
          form.elements["request_timeout_seconds"].value = source.request_timeout_seconds ?? 20;
          form.elements["enabled"].checked = Boolean(source.enabled);
          form.querySelector('button[type="submit"]').textContent = "更新 RSS 源";
        },
      });
      return;
    }

    const testRssSourceId = target.dataset.testRssSource;
    if (testRssSourceId) {
      showToast("正在测试 RSS 源...", "info", 5000);
      try {
        const result = await api(`/rss/sources/${testRssSourceId}/test`, { method: "POST" });
        showToast(`RSS 源测试成功：获取 ${result.fetched_count || 0} 条`, "ok", 6000);
        setOutput(result);
      } catch (err) {
        showToast(err.message || "RSS 源测试失败", "fail", 8000);
        setOutput(`❌ ${err.message || err}`);
      }
      return;
    }

    const previewRssSourceId = target.dataset.previewRssSource;
    if (previewRssSourceId) {
      const result = await api(`/rss/sources/${previewRssSourceId}/preview?limit=8`);
      setOutput(result);
      return;
    }

    const disableRssSourceId = target.dataset.disableRssSource;
    if (disableRssSourceId) {
      const source = state.rss.find((item) => String(item.id) === String(disableRssSourceId));
      const label = source?.name ? `「${source.name}」` : `ID ${disableRssSourceId}`;
      if (!window.confirm(`确认停用 RSS 源 ${label}？停用后不会继续采集，但历史数据会保留。`)) {
        return;
      }
      const result = await api(`/rss/sources/${disableRssSourceId}/disable`, { method: "POST" });
      await refreshRSS();
      setOutput(result);
      return;
    }

    const deleteRssSourceId = target.dataset.deleteRssSource;
    if (deleteRssSourceId) {
      const source = state.rss.find((item) => String(item.id) === String(deleteRssSourceId));
      const label = source?.name ? `「${source.name}」` : `ID ${deleteRssSourceId}`;
      if (
        !window.confirm(
          `确认永久删除 RSS 源 ${label}？该源的 RSS 采集明细也会删除；已生成报告和视频不会删除。`,
        )
      ) {
        return;
      }
      const result = await api(`/rss/sources/${deleteRssSourceId}/delete`, { method: "POST" });
      const form = document.getElementById("rss-form");
      if (form?.elements["id"]?.value === String(deleteRssSourceId)) {
        resetRSSForm();
      }
      await refreshRSS();
      setOutput(result);
      return;
    }

    const runScheduleId = target.dataset.runSchedule;
    if (runScheduleId) {
      const result = await api(`/schedules/${runScheduleId}/run`, { method: "POST" });
      await refreshRuns();
      setOutput(result);
      return;
    }

    const disableScheduleId = target.dataset.disableSchedule;
    if (disableScheduleId) {
      const schedule = state.schedules.find((item) => String(item.id) === String(disableScheduleId));
      if (!schedule) {
        showToast("没有找到定时计划，请刷新后重试。", "fail");
        return;
      }
      const action = schedule.enabled ? "disable" : "enable";
      const result = await api(`/schedules/${disableScheduleId}/${action}`, { method: "POST" });
      await refreshSchedules();
      setOutput(result);
      return;
    }

    const editScheduleId = target.dataset.editSchedule;
    if (editScheduleId) {
      const schedule = state.schedules.find((item) => String(item.id) === String(editScheduleId));
      if (!schedule) {
        showToast("没有找到定时计划，请刷新后重试。", "fail");
        return;
      }
      openDialog("schedule-dialog", {
        title: `编辑定时计划：${schedule.name}`,
        populate: () => {
          const form = document.getElementById("schedule-form");
          form.elements["id"].value = schedule.id || "";
          form.elements["name"].value = schedule.name || "";
          form.elements["task_type"].value = schedule.task_type || "daily_report";
          form.elements["cron_expression"].value = schedule.cron_expression || "";
          form.elements["timezone"].value = schedule.timezone || "Asia/Shanghai";
          form.elements["rss_category"].value = schedule.rss_category || "";
          form.elements["model_config_id"].value = schedule.model_config_id || "";
          form.elements["prompt_version_id"].value = schedule.prompt_version_id || "";
          form.elements["tts_config_id"].value = schedule.tts_config_id || "";
          form.elements["render_engine"].value = schedule.render_engine === "ffmpeg" ? "ffmpeg" : "remotion";
          form.elements["report_type"].value = schedule.report_type || "general";
          form.elements["enabled"].checked = Boolean(schedule.enabled);
          form.elements["prevent_overlap"].checked = Boolean(schedule.prevent_overlap);
          form.elements["auto_render"].checked = schedule.auto_render === undefined ? true : Boolean(schedule.auto_render);
          form.elements["auto_publish"].checked = Boolean(schedule.auto_publish);
          form.elements["max_runtime_seconds"].value = schedule.max_runtime_seconds || 3600;
          form.elements["retry_count"].value = schedule.retry_count || 0;
          form.elements["retry_interval_seconds"].value = schedule.retry_interval_seconds || 300;
          form.querySelector('button[type="submit"]').textContent = "更新计划";
        },
      });
      return;
    }

    const runEventsButton = target.closest("[data-run-events]");
    const runEventsId = runEventsButton?.dataset.runEvents;
    if (runEventsId) {
      await loadRunEvents(runEventsId);
      return;
    }

    const runLLMLogsId = target.dataset.runLlmLogs;
    if (runLLMLogsId) {
      $("#llm-log-run-filter").value = runLLMLogsId;
      document.querySelector('[data-tab="llm-logs"]').click();
      await refreshLLMPanel();
      return;
    }

    const cancelRunId = target.dataset.cancelRun;
    if (cancelRunId) {
      const result = await api(`/runs/${cancelRunId}/cancel`, { method: "POST" });
      await refreshRuns();
      if (state.selectedRunId === Number(cancelRunId)) {
        await loadRunEvents(cancelRunId);
      }
      setOutput(result);
      return;
    }

    const retryRunId = target.dataset.retryRun;
    if (retryRunId) {
      const result = await api(`/runs/${retryRunId}/retry`, { method: "POST" });
      await refreshRuns();
      setOutput(result);
      return;
    }

    const runRow = target.closest("tr[data-run-row]");
    const isInteractive = target.closest("button, a, input, select, textarea, label");
    if (runRow && !isInteractive) {
      await loadRunEvents(runRow.dataset.runRow);
      return;
    }

    const editModelId = target.dataset.editModel;
    if (editModelId) {
      const model = state.models.find((item) => String(item.id) === String(editModelId));
      if (!model) {
        showToast("没有找到模型配置，请刷新后重试。", "fail");
        return;
      }
      openDialog("model-dialog", {
        title: `编辑模型：${model.name}`,
        populate: () => {
          const form = document.getElementById("model-form");
          form.elements["id"].value = model.id || "";
          form.elements["name"].value = model.name || "";
          form.elements["base_url"].value = model.base_url || "";
          form.elements["model_name"].value = model.model_name || "";
          form.elements["model_type"].value = model.model_type || "llm";
          form.elements["voice"].value = model.voice || "";
          form.elements["timeout_seconds"].value = model.timeout_seconds || 180;
          form.elements["temperature"].value = model.temperature || 0.2;
          form.elements["enabled"].checked = Boolean(model.enabled);
          form.elements["is_default"].checked = Boolean(model.is_default);
          form.elements["api_key"].value = "";
          form.elements["api_key"].placeholder = "留空表示不修改";
          form.querySelector('button[type="submit"]').textContent = "更新模型";
          updateModelFormVisibility();
        },
      });
      return;
    }

    const testModelId = target.dataset.testModel;
    if (testModelId) {
      setOutput("⏳ 正在测试 API 连接...");
      showToast("正在测试 API 连接...", "info", 5000);
      setBusy(target, true);
      try {
        const result = await api(`/model-configs/${testModelId}/test`, { method: "POST" });
        const msg = result.message || "API 测试成功";
        showToast(msg, "ok", 6000);
        setOutput(`✅ ${msg}\n\n${JSON.stringify(result, null, 2)}`);
      } catch (err) {
        showToast(err.message || "API 测试失败", "fail", 8000);
        setOutput(`❌ ${err.message || err}`);
      } finally {
        setBusy(target, false);
      }
      return;
    }

    const deleteModelId = target.dataset.deleteModel;
    if (deleteModelId) {
      const model = state.models.find((item) => String(item.id) === String(deleteModelId));
      const name = model ? model.name : `#${deleteModelId}`;
      if (!window.confirm(`确认删除模型配置「${name}」？被启用定时计划引用的模型不会被删除。`)) {
        return;
      }
      const result = await api(`/model-configs/${deleteModelId}`, { method: "DELETE" });
      await refreshModels();
      await refreshTTSVoices();
      setOutput(result);
      return;
    }

    const editPromptId = target.dataset.editPrompt;
    if (editPromptId) {
      const prompt = state.prompts.find((item) => String(item.id) === String(editPromptId));
      if (!prompt) {
        showToast("没有找到提示词版本，请刷新后重试。", "fail");
        return;
      }
      openDialog("prompt-dialog", {
        title: `编辑提示词：${prompt.name}`,
        populate: () => {
          const form = document.getElementById("prompt-form");
          form.elements["id"].value = prompt.id || "";
          form.elements["name"].value = prompt.name || "";
          form.elements["system_prompt"].value = prompt.system_prompt || "";
          form.elements["user_prompt_template"].value = prompt.user_prompt_template || "";
          form.elements["enabled"].checked = Boolean(prompt.enabled);
          form.elements["is_default"].checked = Boolean(prompt.is_default);
          form.querySelector('button[type="submit"]').textContent = "更新提示词";
        },
      });
      return;
    }

    const deletePromptId = target.dataset.deletePrompt;
    if (deletePromptId) {
      const prompt = state.prompts.find((item) => String(item.id) === String(deletePromptId));
      const name = prompt ? prompt.name : `#${deletePromptId}`;
      if (!window.confirm(`确认删除提示词「${name}」？引用此提示词的定时计划和任务记录不会删除，但关联会失效。`)) {
        return;
      }
      const result = await api(`/prompt-versions/${deletePromptId}`, { method: "DELETE" });
      await refreshPrompts();
      setOutput(result);
      return;
    }

    const publishReportId = target.dataset.publishReport;
    if (publishReportId) {
      const result = await api(`/reports/${publishReportId}/publish`, { method: "POST" });
      await refreshReports();
      if (state.selectedReportId === Number(publishReportId)) {
        await loadReportDetail(publishReportId);
      }
      setOutput(result);
      return;
    }

    const renderReportId = target.dataset.renderReport;
    if (renderReportId) {
      const engineSelect = document.querySelector(`[data-render-engine-for="${renderReportId}"]`);
      const engine = engineSelect?.value || "remotion";
      const result = await api(`/render/report/${renderReportId}?engine=${encodeURIComponent(engine)}`, { method: "POST" });
      await refreshRuns();
      await refreshReports();
      await refreshVideos();
      setOutput(result);
      return;
    }

    const watchVideoJobId = target.dataset.watchVideoJob;
    if (watchVideoJobId) {
      const result = await api(`/jobs/${watchVideoJobId}`);
      state.selectedReportId = Number(result.report_id);
      startVideoJobStream(watchVideoJobId);
      setOutput(result);
      return;
    }

    const cancelVideoJobId = target.dataset.cancelVideoJob;
    if (cancelVideoJobId) {
      const result = await api(`/jobs/${cancelVideoJobId}/cancel`, { method: "POST" });
      await refreshReports();
      await refreshVideos();
      setOutput(result);
      return;
    }

    const generateCoverReportId = target.dataset.generateCover;
    if (generateCoverReportId) {
      const result = await api(`/video-assets/${generateCoverReportId}/cover`, { method: "POST" });
      await refreshVideos();
      await refreshReports();
      setOutput(result);
      return;
    }

    const deleteVideoAssetsReportId = target.dataset.deleteVideoAssets;
    if (deleteVideoAssetsReportId) {
      const confirmed = window.confirm(
        "确认删除这个报告的成品视频、封面和音频缓存？数据库中的报告和新闻不会删除，可重新渲染生成。",
      );
      if (!confirmed) {
        return;
      }
      const result = await api(`/video-assets/${deleteVideoAssetsReportId}?delete_audio=true`, {
        method: "DELETE",
      });
      await refreshReports();
      await refreshVideos();
      if (state.selectedReportId === Number(deleteVideoAssetsReportId)) {
        await loadReportDetail(deleteVideoAssetsReportId);
      }
      setOutput(result);
      return;
    }

    const reportDetailId = target.dataset.reportDetail;
    if (reportDetailId) {
      await loadReportDetail(reportDetailId);
      return;
    }

    const reportRow = target.closest("tr[data-report-row]");
    if (reportRow && !target.closest("button, a, input, select, textarea, label")) {
      await loadReportDetail(reportRow.dataset.reportRow);
      return;
    }

    const generateTTSId = target.dataset.generateTts;
    if (generateTTSId) {
      const result = await api(`/tts-items/${generateTTSId}/generate`, { method: "POST" });
      await refreshTTSVoices();
      if (state.selectedReportId) {
        await loadReportDetail(state.selectedReportId);
      }
      setOutput(result);
      return;
    }

    const retryTTSId = target.dataset.retryTts;
    if (retryTTSId) {
      const result = await api(`/tts-items/${retryTTSId}/retry`, { method: "POST" });
      await refreshTTSVoices();
      if (state.selectedReportId) {
        await loadReportDetail(state.selectedReportId);
      }
      setOutput(result);
      return;
    }

    const deleteTTSAudioId = target.dataset.deleteTtsAudio;
    if (deleteTTSAudioId) {
      if (!window.confirm(`确认删除语音音频文件 #${deleteTTSAudioId}？删除后可重新生成。`)) {
        return;
      }
      const result = await api(`/tts-items/${deleteTTSAudioId}/audio`, { method: "DELETE" });
      await refreshTTSVoices();
      if (state.selectedReportId) {
        await loadReportDetail(state.selectedReportId);
      }
      setOutput(result);
      return;
    }

    const editReportNewsId = target.dataset.editReportNews;
    if (editReportNewsId) {
      showNewsEditForm(editReportNewsId);
    }
    } catch (error) {
      console.error("[click-handler] error:", error);
      showToast(error.message || "操作失败", "fail", 8000);
      setOutput(`❌ 操作失败：${error.message || error}`);
    } finally {
      setBusy(target, false);
    }
  });
}

function hideNewsEditForm() {
  $("#news-edit-form").classList.add("hidden");
}

function showNewsEditForm(newsId) {
  const item = state.selectedReportNews.find((news) => String(news.id) === String(newsId));
  if (!item) {
    setOutput("没有找到新闻，请刷新报告详情后重试。");
    return;
  }
  const form = $("#news-edit-form");
  form.elements.id.value = item.id || "";
  form.elements.item_index.value = item.item_index || 1;
  form.elements.importance.value = item.importance || "中";
  form.elements.pubdate.value = item.pubdate || "";
  form.elements.title.value = item.title || "";
  form.elements.related_field.value = item.related_field || "";
  form.elements.summary.value = item.summary || "";
  form.elements.voiceover_script.value = item.voiceover_script || "";
  form.elements.reserve_reason.value = item.reserve_reason || "";
  form.elements.link.value = item.link || "";
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadReportDetail(reportId) {
  closeVideoJobStream();
  state.selectedReportId = Number(reportId);
  renderReportsTable();
  const data = await api(`/reports/${reportId}`);
  const report = data.report;
  const news = data.news || [];
  state.selectedReportNews = news;
  $("#report-empty").style.display = "none";
  $("#report-detail-content").classList.remove("hidden");
  $("#report-actions").innerHTML = reportActions(report, "detail");
  $("#report-meta").innerHTML = `
    <h4>${escapeHTML(report.title)}</h4>
    <p>${escapeHTML(report.daily_trend)}</p>
    <dl>
      <div><dt>新闻数量</dt><dd>${fmt(report.key_news_count)}</dd></div>
      <div><dt>TTS 数量</dt><dd>${fmt(report.tts_item_count)}</dd></div>
      <div><dt>已发布</dt><dd>${fmt(report.published_count)}</dd></div>
    </dl>
    <div id="report-video-live">${reportVideoLive(report)}</div>
  `;
  if (report.video_job_id && !isTerminalVideoJob(report.video_status)) {
    startVideoJobStream(report.video_job_id);
  }
  $("#report-news-list").innerHTML = news
    .map(
      (item) => `
        <article class="news-item">
          <div class="news-item-head">
            <span class="${statusClass(item.importance || "pending")}">${escapeHTML(item.importance)}</span>
            <strong>${escapeHTML(item.title)}</strong>
          </div>
          <div class="muted">${escapeHTML(item.pubdate)} · ${escapeHTML(item.related_field)} · ${item.published ? "已发布" : "未发布"} · TTS ${escapeHTML(item.tts_status)}</div>
          <div class="detail-actions news-actions">
            <button class="link-button" data-edit-report-news="${item.id}" type="button">编辑</button>
            ${ttsActions(item)}
          </div>
          ${item.tts_error ? `<div class="form-error">${escapeHTML(item.tts_error)}</div>` : ""}
          <p>${escapeHTML(item.summary)}</p>
          <p><strong>口播稿：</strong>${escapeHTML(item.voiceover_script)}</p>
          <p><strong>保留理由：</strong>${escapeHTML(item.reserve_reason)}</p>
          ${
            item.link
              ? `<a href="${escapeHTML(item.link)}" target="_blank" rel="noreferrer">原文链接</a>`
              : ""
          }
        </article>
      `,
    )
    .join("");
}

async function loadRunEvents(runId) {
  closeRunEventStream();
  state.selectedRunId = Number(runId);
  const data = await api(`/runs/${runId}/events`);
  state.selectedRunEvents = data.events || [];
  renderRunsTable();
  renderRunEvents();
  startRunEventStream(runId);
}

async function refreshSelectedRunEvents(runId) {
  state.selectedRunId = Number(runId);
  const data = await api(`/runs/${runId}/events`);
  state.selectedRunEvents = data.events || [];
  renderRunsTable();
  renderRunEvents();
}

function closeRunEventStream() {
  if (state.runEventSource) {
    state.runEventSource.close();
    state.runEventSource = null;
  }
}

function upsertRun(run) {
  if (!run) {
    return;
  }
  const index = state.runs.findIndex((item) => Number(item.id) === Number(run.id));
  if (index >= 0) {
    state.runs[index] = run;
  } else {
    state.runs.unshift(run);
  }
  renderRunsTable();
}

function applyRunStreamPayload(payload, replaceEvents = false) {
  upsertRun(payload.run);
  const incomingEvents = payload.events || [];
  if (replaceEvents) {
    state.selectedRunEvents = incomingEvents;
  } else if (incomingEvents.length) {
    const existingIds = new Set(state.selectedRunEvents.map((item) => Number(item.id)));
    state.selectedRunEvents = [
      ...state.selectedRunEvents,
      ...incomingEvents.filter((item) => !existingIds.has(Number(item.id))),
    ];
  }
  renderRunEvents();
  if (isTerminalRun(payload.run?.status)) {
    closeRunEventStream();
  }
}

function startRunEventStream(runId) {
  if (!window.EventSource) {
    return;
  }
  closeRunEventStream();
  const source = new EventSource(`/runs/${runId}/stream`);
  state.runEventSource = source;

  source.addEventListener("snapshot", (event) => {
    applyRunStreamPayload(JSON.parse(event.data), true);
  });

  source.addEventListener("update", (event) => {
    applyRunStreamPayload(JSON.parse(event.data), false);
  });

  source.addEventListener("error", () => {
    if (state.runEventSource === source) {
      closeRunEventStream();
    }
  });
}

function closeVideoJobStream() {
  if (state.videoJobEventSource) {
    state.videoJobEventSource.close();
    state.videoJobEventSource = null;
  }
}

function applyVideoJobStreamPayload(payload) {
  const job = payload.job;
  if (!job) {
    return;
  }
  if (Number(job.report_id) !== Number(state.selectedReportId)) {
    return;
  }
  const live = $("#report-video-live");
  if (live) {
    live.innerHTML = reportVideoLive({
      video_status: job.status,
      video_progress_percent: job.progress_percent,
      video_current_step: job.current_step,
      video_error_message: job.error_message,
    });
  }
  const reportIndex = state.reports.findIndex((item) => Number(item.id) === Number(job.report_id));
  if (reportIndex >= 0) {
    state.reports[reportIndex] = {
      ...state.reports[reportIndex],
      video_job_id: job.id,
      video_status: job.status,
      video_progress_percent: job.progress_percent,
      video_current_step: job.current_step,
      video_path: job.video_path,
      duration_seconds: job.duration_seconds,
    };
    renderReportsTable();
  }
  const jobIndex = state.videoJobs.findIndex((item) => Number(item.id) === Number(job.id));
  if (jobIndex >= 0) {
    state.videoJobs[jobIndex] = {
      ...state.videoJobs[jobIndex],
      ...job,
    };
    renderVideosTable();
  }
  if (isTerminalVideoJob(job.status)) {
    closeVideoJobStream();
  }
}

function startVideoJobStream(jobId) {
  if (!window.EventSource) {
    return;
  }
  closeVideoJobStream();
  const source = new EventSource(`/jobs/${jobId}/stream`);
  state.videoJobEventSource = source;

  source.addEventListener("snapshot", (event) => {
    applyVideoJobStreamPayload(JSON.parse(event.data));
  });

  source.addEventListener("update", (event) => {
    applyVideoJobStreamPayload(JSON.parse(event.data));
  });

  source.addEventListener("error", () => {
    if (state.videoJobEventSource === source) {
      closeVideoJobStream();
    }
  });
}

async function boot() {
  configureApi({ onUnauthorized: showLogin });
  bindTabs();
  bindDialogs();
  bindForms();
  bindActions();
  const canLoad = await refreshAuth();
  restoreActiveTab();
  if (canLoad) {
    await refreshAll();
  } else {
    await refreshHealth();
  }
  window.setInterval(async () => {
    await refreshHealth();
    if (state.auth.auth_enabled && !state.auth.authenticated) {
      return;
    }
    await refreshRuns();
    await refreshLLMPanel();
    await refreshReports();
    if (!state.videoJobEventSource) {
      await refreshVideos();
    }
    if (state.selectedRunId && !state.runEventSource) {
      await refreshSelectedRunEvents(state.selectedRunId);
    }
    if (state.selectedReportId && !state.videoJobEventSource) {
      await loadReportDetail(state.selectedReportId);
    }
  }, 5000);
}

boot().catch((error) => {
  setOutput(error.message);
});
