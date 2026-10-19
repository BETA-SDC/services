import assert from "node:assert/strict";
import {test, afterEach} from "node:test";

globalThis.location = {pathname: "/checkin"};
const {model, prefilled} = await import("../checkin/context.js");
const {api, fieldLabel, answerText, localTime, accessStatus, busy} = await import("../checkin/ui.js");
const {clearSensitive} = await import("../checkin/sensitive.js");
const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  model.state = undefined;
  prefilled.clear();
});

test("shared API preserves CSRF, same-origin credentials and header-only PAT", async () => {
  model.state = {csrf_token: "synthetic-csrf"};
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({ok: true})};
  };
  await api("/api/checkin/events", {title: "Test"}, "PUT");
  await api("/api/checkin/events/test/entries", undefined, "GET", {"X-Checkin-PAT": "synthetic-pat"});
  assert.equal(calls[0].options.headers["X-CSRF-Token"], "synthetic-csrf");
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(calls[0].options.cache, "no-store");
  assert.deepEqual(JSON.parse(calls[0].options.body), {title: "Test"});
  assert.equal(calls[1].options.headers["X-Checkin-PAT"], "synthetic-pat");
  assert.equal(calls[1].options.body, undefined);
  assert.equal(calls[1].url.includes("synthetic-pat"), false);
});

test("API errors preserve authorization error codes", async () => {
  globalThis.fetch = async () => ({
    ok: false, status: 401,
    json: async () => ({error: "Verify", code: "second_factor_required"}),
  });
  await assert.rejects(api("/api/checkin/events/test/entries"),
    error => error.status === 401 && error.code === "second_factor_required" && error.message === "Verify");
});

test("field labels, document formatting and Beijing times are shared", () => {
  model.state = {documents: {passport: "Passport"}};
  assert.equal(fieldLabel({custom_fields: [{id: "custom_1", label: "Diet"}]}, "custom_1"), "Diet");
  assert.equal(fieldLabel({}, "student_id"), "学号");
  assert.equal(answerText({type: "passport", country: "CN", number: "TEST123"}), "Passport / CN / TEST123");
  assert.equal(localTime(Date.parse("2026-10-19T00:00:00Z")), "2026-10-19T08:00");
  assert.equal(accessStatus({status: "approved", revoked_at: 1}), "已撤销");
  assert.equal(accessStatus({status: "approved", expires_at: 1}), "已到期");
});

test("sensitive reset clears in-memory PAT, prefills, table and expiry timer", () => {
  const elements = new Map();
  const input = {value: "13800138000"};
  for (const key of ["#record-pat", "#record-section", "#records-body"]) {
    elements.set(key, {value: "secret", hidden: false, replaceChildren() { this.cleared = true; }});
  }
  elements.set('#answer-fields input[name="phone"]', input);
  globalThis.document = {querySelector: selector => elements.get(selector)};
  model.recordPAT = "synthetic-pat";
  model.recordEvent = {can_export: true};
  model.recordExpiryTimer = setTimeout(() => assert.fail("Expiry timer was not cleared"), 1000);
  prefilled.add("phone");
  clearSensitive();
  assert.equal(model.recordPAT, "");
  assert.equal(model.recordEvent, null);
  assert.equal(elements.get("#record-pat").value, "");
  assert.equal(elements.get("#record-section").hidden, true);
  assert.equal(elements.get("#records-body").cleared, true);
  assert.equal(input.value, "");
  assert.equal(prefilled.size, 0);
});

test("busy controls restore pagination boundaries even on errors", async () => {
  const error = {textContent: "", hidden: true};
  globalThis.document = {querySelector: () => error};
  model.page = 1;
  const button = {id: "previous", disabled: false};
  await busy(button, async () => { throw new Error("Synthetic failure"); });
  assert.equal(button.disabled, true);
  assert.equal(error.textContent, "Synthetic failure");
  assert.equal(error.hidden, false);
});
