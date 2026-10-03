import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiJson, auth } from "./api";

type Reply = { status: number; body?: unknown };

/** Fake fetch: answers by path from a queue, and records the paths called. */
function mockFetch(replies: Record<string, Reply[]>) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    const reply = replies[url]?.shift() ?? { status: 404 };
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
    });
  });
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("apiFetch", () => {
  it("refreshes once on 401 and retries", async () => {
    const calls = mockFetch({
      "/api/dashboard": [{ status: 401 }, { status: 200, body: { ok: true } }],
      "/api/auth/refresh": [{ status: 200 }],
    });
    await expect(apiJson("/dashboard")).resolves.toEqual({ ok: true });
    expect(calls).toEqual(["/api/dashboard", "/api/auth/refresh", "/api/dashboard"]);
  });

  it("gives up when the refresh fails", async () => {
    const calls = mockFetch({
      "/api/dashboard": [{ status: 401, body: { detail: "Not authenticated." } }],
      "/api/auth/refresh": [{ status: 401 }],
    });
    await expect(apiJson("/dashboard")).rejects.toMatchObject({ status: 401 });
    expect(calls).toEqual(["/api/dashboard", "/api/auth/refresh"]);
  });

  it("parallel 401s share one refresh (the token rotates)", async () => {
    const calls = mockFetch({
      "/api/a": [{ status: 401 }, { status: 200, body: 1 }],
      "/api/b": [{ status: 401 }, { status: 200, body: 2 }],
      "/api/auth/refresh": [{ status: 200 }],
    });
    await expect(Promise.all([apiJson("/a"), apiJson("/b")])).resolves.toEqual([1, 2]);
    expect(calls.filter((c) => c === "/api/auth/refresh")).toHaveLength(1);
  });

  it("a wrong password never triggers a refresh and shows the API's one message", async () => {
    const calls = mockFetch({
      "/api/auth/login": [{ status: 401, body: { detail: "Invalid email or password." } }],
    });
    const error = await auth.login("x@y.z", "bad").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("Invalid email or password.");
    expect(calls).toEqual(["/api/auth/login"]);
  });

  it("a validation error (422 list) gets a readable message", async () => {
    mockFetch({
      "/api/auth/login": [{ status: 422, body: { detail: [{ loc: ["body", "password"] }] } }],
    });
    await expect(auth.login("x@y.z", "")).rejects.toThrow("Some fields are missing or invalid.");
  });

  it("an unreachable API gets a plain message, not a stack trace", async () => {
    mockFetch({ "/api/auth/me": [{ status: 502 }] });
    await expect(auth.me()).rejects.toThrow("The server is unavailable. Is the API running?");
  });
});
