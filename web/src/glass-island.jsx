// Liquid Glass React 岛入口。
// 由 esbuild 打包成 web/glass-island.js（ESM，react/react-dom/liquid-glass-react 全部 inline）。
// 运行时仍是纯静态文件，挂在 /console 下，由 index.html 在 app.js 之前引入。
//
// 控制台主体保持原生 JS；本岛仅负责把 data-glass="<slot>" 容器渲染为 <LiquidGlass> 包裹的内容，
// 通过 window.glassBridge 与原生 app.js 双向通信：
//   - app.js: glassBridge.set(slot, { html?, ...state }) 推内容；glassBridge.on('event', fn) 注册处理。
//   - 岛:     glassBridge.subscribe(slot, fn) 订阅内容变化；glassBridge.emit('event', payload) 触发处理。

import { useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import LiquidGlass from "liquid-glass-react";

// ---------------------------------------------------------------------------
// Glass Bridge：极简发布订阅，原生 ↔ React 通信
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
// 深浅模式：控制台仅按 prefers-color-scheme 切换，据此决定 overLight
// ---------------------------------------------------------------------------
function useIsDark() {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const subscribe = (cb) => {
    query.addEventListener("change", cb);
    return () => query.removeEventListener("change", cb);
  };
  const getSnapshot = () => query.matches;
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

// 订阅某个 bridge slot 的状态
function useSlot(slot) {
  return useSyncExternalStore(
    (cb) => glassBridge.subscribe(slot, cb),
    () => glassBridge.get(slot),
    () => glassBridge.get(slot),
  );
}

// 默认玻璃参数（保守简洁，避免视觉失真和交互问题）
function defaultGlassProps(slot, overLight) {
  const base = {
    displacementScale: 8,
    blurAmount: 0.02,
    saturation: 105,
    aberrationIntensity: 0,
    elasticity: 0,
    cornerRadius: 18,
    overLight,
  };
  // 实时流式表格（runs / video jobs 走 SSE）进一步降档，避免逐像素位移图频繁重算卡顿
  if (slot === "runs-table" || slot === "run-events" || slot === "videos-table") {
    return { ...base, displacementScale: 4, blurAmount: 0.01 };
  }
  return base;
}

// 通用玻璃盒：把 bridge 推来的 html 字符串用 dangerouslySetInnerHTML 作为 <LiquidGlass> 子节点，
// 从而对 renderers.js 产出的表格/卡片内容获得真实折射，无需把渲染器改写成 React 元素。
function GlassBox({ slot, glassProps }) {
  const isDark = useIsDark();
  const state = useSlot(slot);
  const html = state?.html ?? "";
  return (
    <LiquidGlass {...(glassProps ?? defaultGlassProps(slot, !isDark))}>
      <div className="glass-content" dangerouslySetInnerHTML={{ __html: html }} />
    </LiquidGlass>
  );
}

// ---------------------------------------------------------------------------
// 顶栏状态簇岛：auth-state + 退出 + health，扁平样式（不使用 LiquidGlass）
// state: { authText, authStatus, logoutVisible, healthText, healthStatus }
// ---------------------------------------------------------------------------
function TopbarIsland() {
  const s = useSlot("topbar");
  const authStatus = s?.authStatus ?? "";
  const healthStatus = s?.healthStatus ?? "";
  return (
    <div className="topbar-actions-inner">
      <span className={`topbar-badge ${healthStatus}`}>
        <svg className="topbar-badge-icon" viewBox="0 0 8 8" width="8" height="8" aria-hidden="true">
          <circle cx="4" cy="4" r="4" />
        </svg>
        {s?.healthText ?? "检查服务中"}
      </span>
      <span className={`topbar-badge ${authStatus}`}>
        <svg className="topbar-badge-icon" viewBox="0 0 8 8" width="8" height="8" aria-hidden="true">
          <circle cx="4" cy="4" r="4" />
        </svg>
        {s?.authText ?? "检查登录中"}
      </span>
      <button
        type="button"
        className={`topbar-logout ${s?.logoutVisible ? "" : "hidden"}`}
        onClick={() => glassBridge.emit("logout")}
      >
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M6 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
          <path d="M11 5l3 3-3 3" />
          <path d="M14 8H6" />
        </svg>
        退出
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 登录卡片岛：表单使用 CSS glassmorphism（纯样式，避免 SVG filter 干扰输入交互）
// state: { error }
// ---------------------------------------------------------------------------
function LoginIsland() {
  const s = useSlot("login");
  const error = s?.error ?? "";
  function handleSubmit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {};
    for (const [key, value] of new FormData(form).entries()) {
      payload[key] = value;
    }
    glassBridge.emit("submit:login", payload);
  }
  return (
    <form className="login-card" onSubmit={handleSubmit}>
      <h2>登录控制台</h2>
      <p>请输入管理员账号后继续管理 RSS、模型和视频任务。</p>
      <label>
        用户名
        <input name="username" autoComplete="username" required />
      </label>
      <label>
        密码
        <input name="password" autoComplete="current-password" required type="password" />
      </label>
      <button type="submit">登录</button>
      <div className="form-error" role="alert">{error}</div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Toast 岛：渲染 toast 列表，纯 CSS 样式（不使用 LiquidGlass）
// state: { toasts: [{ id, msg, type }] }
// ---------------------------------------------------------------------------
function ToastIsland() {
  const s = useSlot("toast");
  const toasts = s?.toasts ?? [];
  return (
    <>
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast ${t.type || "info"}`}
          onClick={() => glassBridge.emit("dismiss:toast", { id: t.id })}
          role="status"
        >
          <span className="toast-msg">{t.msg}</span>
        </div>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// 岛注册表与挂载
// ---------------------------------------------------------------------------
const ISLAND_COMPONENTS = {
  topbar: TopbarIsland,
  login: LoginIsland,
  toast: ToastIsland,
};

function mountIslands() {
  const containers = document.querySelectorAll("[data-glass]");
  containers.forEach((container) => {
    const slot = container.dataset.glass;
    const Component = ISLAND_COMPONENTS[slot] || (() => <GlassBox slot={slot} />);
    const root = createRoot(container);
    root.render(<Component />);
    container.__glassRoot = root;
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountIslands, { once: true });
} else {
  mountIslands();
}
