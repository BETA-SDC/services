import assert from "node:assert/strict";
import {afterEach, test} from "node:test";
import {HTTPError, requestJSON} from "../shared/http.js";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test("JSON client keeps credentials, no-store, timeout and CSRF without persisting secrets", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    return {ok: true, status: 200, json: async () => ({csrf_token: "rotated"})};
  };
  assert.deepEqual(await requestJSON("/api/profile", {data: {nickname: "Test"}, csrf: "synthetic", method: "PUT"}),
    {csrf_token: "rotated"});
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.headers["X-CSRF-Token"], "synthetic");
  assert.ok(calls[0].options.signal);
  await requestJSON("/api/session");
  assert.equal(calls[1].options.method, "GET");
  assert.equal(calls[1].options.body, undefined);
  assert.equal(calls[1].options.headers["X-CSRF-Token"], undefined);
});

test("JSON client preserves authorization codes and HTTP status", async () => {
  globalThis.fetch = async () => ({ok: false, status: 403, json: async () =>
    ({error: "Permission denied", code: "role_required"})});
  await assert.rejects(requestJSON("/api/admin/overview"), error =>
    error instanceof HTTPError && error.status === 403 && error.code === "role_required"
      && error.message === "Permission denied");
});

test("HTML proxy errors and malformed JSON get bounded user-facing errors", async () => {
  for (const status of [429, 502]) {
    globalThis.fetch = async () => ({ok: false, status, json: async () => {
      throw new Error("HTML containing internal proxy details");
    }});
    await assert.rejects(requestJSON("/api/session"), error =>
      error.status === status && !error.message.includes("internal"));
  }
  for (const payload of [null, [], "invalid"]) {
    globalThis.fetch = async () => ({ok: true, status: 200, json: async () => payload});
    await assert.rejects(requestJSON("/api/session"), HTTPError);
  }
});

test("caller cancellation and network errors are preserved", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.signal, controller.signal);
    throw new TypeError("Synthetic network failure");
  };
  await assert.rejects(requestJSON("/api/session", {signal: controller.signal}), TypeError);
});
