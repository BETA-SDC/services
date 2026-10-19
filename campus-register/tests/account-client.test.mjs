import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

const elements = new Map();
globalThis.location = {pathname: "/login", hash: ""};
globalThis.document = {querySelector(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    hidden: true, disabled: false, append() {},
    content: {cloneNode() { return {}; }},
  });
  return elements.get(selector);
}};
const {model} = await import("../account/context.js");
const {api} = await import("../account/client.js");
const {webauthn} = await import("../account/webauthn.js");
const originalFetch = globalThis.fetch;
const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
afterEach(() => {
  globalThis.fetch = originalFetch;
  if (navigatorDescriptor) Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
  else delete globalThis.navigator;
  model.csrfToken = "";
  elements.get("#fields").disabled = false;
});

test("ordinary permission denial does not destroy account CSRF state", async () => {
  model.csrfToken = "synthetic";
  globalThis.fetch = async () => ({
    ok: false, status: 403, json: async () => ({error: "Permission denied"}),
  });
  await assert.rejects(api("/api/profile", {}));
  assert.equal(model.csrfToken, "synthetic");
  assert.equal(elements.get("#fields").disabled, false);
});

test("explicit CSRF failure disables mutations and exposes recovery", async () => {
  model.csrfToken = "expired";
  globalThis.fetch = async () => ({
    ok: false, status: 403, json: async () => ({error: "Expired", code: "csrf_failed"}),
  });
  await assert.rejects(api("/api/profile", {}));
  assert.equal(model.csrfToken, "");
  assert.equal(elements.get("#fields").disabled, true);
  assert.equal(elements.get("#retry").hidden, false);
});

test("successful account requests rotate CSRF", async () => {
  globalThis.fetch = async () => ({ok: true, json: async () => ({csrf_token: "rotated"})});
  await api("/api/session");
  assert.equal(model.csrfToken, "rotated");
});

test("discoverable passkey login decodes options and serializes signed response", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => url.endsWith("/options")
      ? {flow: "synthetic-flow", options: {publicKey: {challenge: "AQID"}}}
      : {ok: true, csrf_token: "rotated"}};
  };
  Object.defineProperty(globalThis, "navigator", {configurable: true, value: {
    credentials: {async get({publicKey}) {
      assert.deepEqual([...publicKey.challenge], [1, 2, 3]);
      return {id: "AQID", rawId: Uint8Array.of(1, 2, 3), type: "public-key",
        response: {clientDataJSON: Uint8Array.of(4), authenticatorData: Uint8Array.of(5),
          signature: Uint8Array.of(6), userHandle: Uint8Array.of(7)},
        getClientExtensionResults: () => ({})};
    }},
  }});
  await webauthn("login", {});
  const payload = JSON.parse(calls[1].options.body);
  assert.equal(calls[0].url, "/api/passkeys/login/options");
  assert.equal(calls[1].url, "/api/passkeys/login/complete");
  assert.equal(payload.flow, "synthetic-flow");
  assert.equal(payload.credential.rawId, "AQID");
  assert.equal(payload.credential.response.signature, "Bg");
  assert.equal(payload.credential.response.userHandle, "Bw");
  assert.equal(model.csrfToken, "rotated");
});
