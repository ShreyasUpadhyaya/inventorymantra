// Fetch wrapper for the FastAPI backend, reached same-origin through /api (next.config.ts).
// Session cookies are HttpOnly: this code never sees a token. On a 401 it asks for a new access
// token once (refresh cookie) and retries; if that fails the session is over.

import type { Role } from "./roles";

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  must_change_password: boolean; // temporary password from the Owner: change it first
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const GENERIC = "Something went wrong. Please try again.";

async function errorFrom(response: Response): Promise<ApiError> {
  let message = GENERIC;
  try {
    const body: unknown = await response.json();
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") message = detail;
    else if (response.status === 422) message = "Some fields are missing or invalid.";
  } catch {
    // not JSON (e.g. the API is down and the proxy answered): keep the generic message
  }
  if (response.status >= 500 && message === GENERIC) {
    message = "The server is unavailable. Is the API running?";
  }
  return new ApiError(response.status, message);
}

let refreshing: Promise<boolean> | null = null;

/** One refresh at a time: parallel 401s share it, so the rotated token isn't reused. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

const NO_RETRY = ["/auth/login", "/auth/refresh", "/auth/logout"];

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = () =>
    fetch(`/api${path}`, {
      ...init,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...init.headers },
    });
  let response = await send();
  if (response.status === 401 && !NO_RETRY.includes(path) && (await refreshSession())) {
    response = await send();
  }
  if (!response.ok) throw await errorFrom(response);
  return response;
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  return (await apiFetch(path, init)).json() as Promise<T>;
}

export const auth = {
  login: (email: string, password: string) =>
    apiJson<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  me: () => apiJson<User>("/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    apiJson<User>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),
  logout: () => apiFetch("/auth/logout", { method: "POST" }).catch(() => undefined),
};
