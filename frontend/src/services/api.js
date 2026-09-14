const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api/v1";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export function getMetricsSummary() {
  return request("/metrics/summary");
}

export function listWebhooks({ status, page = 1, pageSize = 20 } = {}) {
  const params = new URLSearchParams({ page, page_size: pageSize });
  if (status) params.set("status_filter", status);
  return request(`/webhooks?${params.toString()}`);
}

export function getWebhookDetail(eventId) {
  return request(`/webhooks/${encodeURIComponent(eventId)}`);
}

export function retryWebhook(eventId) {
  return request(`/webhooks/${encodeURIComponent(eventId)}/retry`, { method: "POST" });
}
