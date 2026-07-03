let unauthorizedHandler = null;

export function configureApi(options = {}) {
  unauthorizedHandler = options.onUnauthorized || null;
}

export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {};
  }
  if (!response.ok) {
    if (response.status === 401 && unauthorizedHandler) {
      unauthorizedHandler();
    }
    const detail = data.detail || data.message || response.statusText || text;
    const message = typeof detail === "object" && detail !== null
      ? (detail.message || JSON.stringify(detail))
      : detail;
    const error = new Error(message);
    error.detail = detail;
    error.status = response.status;
    throw error;
  }
  return data;
}
