export class HTTPError extends Error {
  constructor(message, status, payload = {}) {
    super(message);
    this.name = "HTTPError";
    this.status = status;
    this.code = payload.code;
    this.email = payload.email;
    this.payload = payload;
  }
}

export async function requestJSON(
  path,
  { data, method, csrf = "", headers = {}, signal } = {},
) {
  const response = await fetch(path, {
    method: method || (data === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    cache: "no-store",
    signal: signal || AbortSignal.timeout(15000),
    headers: {
      ...headers,
      ...(data === undefined
        ? {}
        : { "Content-Type": "application/json", "X-CSRF-Token": csrf }),
    },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  let payload;
  try {
    payload = await response.json();
    if (!payload || typeof payload !== "object" || Array.isArray(payload))
      throw new Error("Invalid JSON response");
  } catch {
    throw new HTTPError(
      response.status === 429
        ? "请求过于频繁，请稍后重试。"
        : "服务暂时不可用，请稍后重试。",
      response.status,
    );
  }
  if (!response.ok) {
    throw new HTTPError(
      payload.error || "请求失败，请稍后重试。",
      response.status,
      payload,
    );
  }
  return payload;
}
