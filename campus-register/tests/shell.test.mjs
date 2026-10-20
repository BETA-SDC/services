import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../beta-shell.js", import.meta.url), "utf8");

function loadShell(session, failed = false) {
  const elements = new Map();
  function element(key) {
    if (!elements.has(key)) elements.set(key, {
      hidden: false, textContent: "", href: "", listeners: {},
      addEventListener(type, listener) { this.listeners[type] = listener; },
      setAttribute() {}, focus() { this.focused = true; },
      querySelector: element,
      querySelectorAll() {
        return ["#beta-summary-link", "#beta-profile-link", "#beta-signout"].map(element);
      },
    });
    return elements.get(key);
  }
  const header = element("header");
  const context = vm.createContext({
    location: {hostname: "account.betasdc.duckdns.org"},
    document: {createElement: element, querySelector() { return null; },
      body: {prepend() {}, append() {}, dataset: {}}, addEventListener() {}, dispatchEvent() {}},
    window: {addEventListener() {}}, URL, CustomEvent: class {},
    matchMedia: () => ({matches: false}),
    fetch: async () => ({ok: !failed, json: async () => session}),
  });
  vm.runInContext(source, context);
  // Match the browser's initial anchor URL, including before session loading.
  const summary = header.innerHTML.match(/<a id="beta-summary-link"[^>]*>/)?.[0];
  assert.ok(summary, "The summary must be a native, keyboard-accessible link");
  assert.match(summary, /role="menuitem"/);
  element("#beta-summary-link").href = summary.match(/href="([^"]+)"/)[1];
  return {context, element};
}

test("anonymous Sign in summary links to login and receives initial menu focus", async () => {
  const {context, element} = loadShell({user: null});
  await context.window.BetaShell.ready;
  assert.equal(element("#beta-name").textContent, "Sign in");
  assert.equal(element("#beta-summary-link").href, "https://account.betasdc.duckdns.org/login");
  assert.equal(element("#beta-signout").hidden, true);
  element("#beta-account-menu").hidden = true;
  element("#beta-account-button").listeners.click();
  assert.equal(element("#beta-summary-link").focused, true);
});

test("summary follows authenticated profile and resets to login after logout", async () => {
  const session = {user: {name: "Demo", profile: {slug: "123"}}};
  const {context, element} = loadShell(session);
  await context.window.BetaShell.ready;
  assert.equal(element("#beta-name").textContent, "Demo");
  assert.equal(element("#beta-summary-link").href,
    "https://profile.betasdc.duckdns.org/sso/start?next=%2F123");
  session.user = null;
  await context.window.BetaShell.refresh();
  assert.equal(element("#beta-summary-link").href, "https://account.betasdc.duckdns.org/login");
});

test("login link remains available when session lookup fails", async () => {
  const {context, element} = loadShell(null, true);
  await context.window.BetaShell.ready;
  assert.equal(element("#beta-summary-link").href, "https://account.betasdc.duckdns.org/login");
});
