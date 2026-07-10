// 控制台岛入口（纯 JS，无框架）。
// 由 index.html 在 app.js 之前引入，挂在 /console/src/islands.js。
//
// 历史上这里是一层 React + liquid-glass-react 的"玻璃岛"，但页面三个槽
// (topbar/login/toast) 实际都未使用 <LiquidGlass>，仅靠 CSS glassmorphism 呈现，
// 1.1MB 的 react/react-dom/liquid-glass-react bundle 形同死代码。现已回归纯 JS。
//
// 职责：把 data-glass="<slot>" 容器渲染为对应内容，并通过 window.glassBridge
// 与原生 app.js 双向通信：
//   - app.js: glassBridge.set(slot, {...state}) 推状态；glassBridge.on('event', fn) 注册处理。
//   - 岛:     glassBridge.subscribe(slot, fn) 订阅状态变化；glassBridge.emit('event', payload) 触发处理。

import { escapeHTML } from "./utils.js?v=20260704-no-glass";

// ---------------------------------------------------------------------------
// Glass Bridge：极简发布订阅，原生 ↔ 岛通信
// ---------------------------------------------------------------------------
function createBridge() {
  const slots = new Map(); // slot -> { state, listeners:Set }
  const handlers = new Map(); // event -> Set<fn>

  function ensure(slot) {
    if (!slots.has(slot)) {
      slots.set(slot, { state: {}, listeners: new Set() });
    }
    return slots.get(slot);
  }

  function set(slot, patch) {
    const s = ensure(slot);
    s.state = { ...s.state, ...patch };
    s.listeners.forEach((fn) => fn(s.state));
  }

  function get(slot) {
    return ensure(slot).state;
  }

  function subscribe(slot, fn) {
    const s = ensure(slot);
    s.listeners.add(fn);
    return () => s.listeners.delete(fn);
  }

  function on(event, fn) {
    if (!handlers.has(event)) handlers.set(event, new Set());
    handlers.get(event).add(fn);
    return () => handlers.get(event)?.delete(fn);
  }

  function emit(event, payload) {
    handlers.get(event)?.forEach((fn) => fn(payload));
  }

  return { set, get, subscribe, on, emit };
}

const glassBridge = createBridge();
window.glassBridge = glassBridge;
export { glassBridge };

// ---------------------------------------------------------------------------
// 顶栏状态簇：auth-state + 退出 + health，扁平样式
// state: { authText, authStatus, logoutVisible, healthText, healthStatus }
// ---------------------------------------------------------------------------
const DOT_SVG =
  '<svg class="topbar-badge-icon" viewBox="0 0 8 8" width="8" height="8" aria-hidden="true"><circle cx="4" cy="4" r="4"></circle></svg>';
const LOGOUT_SVG =
  '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"></path><path d="M11 5l3 3-3 3"></path><path d="M14 8H6"></path></svg>';

function mountTopbar(host) {
  function render() {
    const s = glassBridge.get("topbar");
    const healthStatus = s?.healthStatus ?? "";
    const authStatus = s?.authStatus ?? "";
    const logoutHidden = s?.logoutVisible ? "" : "hidden";
    host.innerHTML = `
      <div class="topbar-actions-inner">
        <span class="topbar-badge ${escapeHTML(healthStatus)}">
          ${DOT_SVG}
          ${escapeHTML(s?.healthText ?? "检查服务中")}
        </span>
        <span class="topbar-badge ${escapeHTML(authStatus)}">
          ${DOT_SVG}
          ${escapeHTML(s?.authText ?? "检查登录中")}
        </span>
        <button type="button" class="topbar-logout ${logoutHidden}" data-action="logout">
          ${LOGOUT_SVG}
          退出
        </button>
      </div>`;
  }
  host.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action='logout']");
    if (target) glassBridge.emit("logout");
  });
  render();
  glassBridge.subscribe("topbar", render);
}

// ---------------------------------------------------------------------------
// 登录卡片：CSS glassmorphism（纯样式，避免 SVG filter 干扰输入交互）
// state: { error }
// ---------------------------------------------------------------------------
function mountLogin(host) {
  host.innerHTML = `
    <form class="login-card">
      <h2>登录控制台</h2>
      <p>请输入管理员账号后继续管理 RSS、模型和视频任务。</p>
      <label>
        用户名
        <input name="username" autocomplete="username" required />
      </label>
      <label>
        密码
        <input name="password" autocomplete="current-password" required type="password" />
      </label>
      <button type="submit">登录</button>
      <div class="form-error" role="alert"></div>
    </form>`;

  const form = host.querySelector(".login-card");
  const errorEl = host.querySelector(".form-error");

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const payload = {};
    for (const [key, value] of new FormData(form).entries()) {
      payload[key] = value;
    }
    glassBridge.emit("submit:login", payload);
  });

  // 仅更新错误文案，避免重渲染打断用户输入
  glassBridge.subscribe("login", (s) => {
    errorEl.textContent = s?.error ?? "";
  });
}

// ---------------------------------------------------------------------------
// Toast：渲染 toast 列表，纯 CSS 样式
// state: { toasts: [{ id, msg, type }] }
// ---------------------------------------------------------------------------
function mountToast(host) {
  // 保留当前 toast 列表引用，点击时按索引回查原始 id（保持类型，避免字符串/数字比较失效）
  let current = [];
  function render() {
    const s = glassBridge.get("toast");
    current = s?.toasts ?? [];
    host.innerHTML = current
      .map(
        (t, i) =>
          `<div class="toast ${escapeHTML(t.type || "info")}" data-idx="${i}" role="status">` +
          `<span class="toast-msg">${escapeHTML(t.msg)}</span>` +
          `</div>`,
      )
      .join("");
  }
  host.addEventListener("click", (event) => {
    const item = event.target.closest(".toast[data-idx]");
    if (!item) return;
    const t = current[Number(item.dataset.idx)];
    if (t) glassBridge.emit("dismiss:toast", { id: t.id });
  });
  render();
  glassBridge.subscribe("toast", render);
}

// ---------------------------------------------------------------------------
// 岛注册表与挂载
// ---------------------------------------------------------------------------
const MOUNTS = {
  topbar: mountTopbar,
  login: mountLogin,
  toast: mountToast,
};

function mountIslands() {
  const containers = document.querySelectorAll("[data-glass]");
  containers.forEach((container) => {
    const slot = container.dataset.glass;
    const mount = MOUNTS[slot];
    if (mount) mount(container);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountIslands, { once: true });
} else {
  mountIslands();
}
