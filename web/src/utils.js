export const $ = (selector) => document.querySelector(selector);

export function fmt(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return String(value).replace("T", " ");
}

export function formatDateTime(value, { compact = false, assumeUTC = true } = {}) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const raw = String(value).trim();
  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  const withZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(normalized)
    ? normalized
    : `${normalized}${assumeUTC ? "Z" : ""}`;
  const date = new Date(withZone);
  if (Number.isNaN(date.getTime())) {
    return fmt(value);
  }
  const pad = (num) => String(num).padStart(2, "0");
  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  const seconds = pad(date.getSeconds());
  return compact
    ? `${month}-${day} ${hours}:${minutes}:${seconds}`
    : `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

export function escapeHTML(value) {
  return fmt(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

export function shortText(value, length = 80) {
  const text = fmt(value);
  return text.length > length ? `${text.slice(0, length)}...` : text;
}

export function statusClass(status) {
  return `status ${String(status || "").toLowerCase()}`;
}
