(() => {
  "use strict";
  const account = "https://account.betasdc.duckdns.org";
  const calendar = "https://calendar.betasdc.duckdns.org";
  const profile = "https://profile.betasdc.duckdns.org";
  const host = location.hostname;
  const header = document.createElement("header");
  header.className = "beta-header";
  header.innerHTML = `<div class="beta-header-left"><button class="beta-icon-button" id="beta-menu-open" type="button" aria-label="Open menu" title="Open menu" aria-haspopup="dialog"><i data-lucide="menu"></i></button><a class="beta-brand" href="${calendar}/sso/start?next=%2Fcalendar%2F"><img src="/brand-logo.png" width="112" height="39" alt="BETA"></a></div><div class="beta-account"><button id="beta-account-button" type="button" aria-label="Open user menu" title="Open user menu" aria-haspopup="menu" aria-expanded="false" aria-controls="beta-account-menu"><img id="beta-avatar" src="/default-avatar.png" width="32" height="32" alt=""></button><div id="beta-account-menu" role="menu" hidden><div class="beta-account-summary"><img id="beta-menu-avatar" src="/default-avatar.png" width="36" height="36" alt=""><span id="beta-name">Sign in</span></div><a id="beta-profile-link" role="menuitem" href="${account}/login"><i data-lucide="user-round"></i><span>Profile</span></a><a role="menuitem" href="${account}/account"><i data-lucide="settings"></i><span>Settings</span></a><button id="beta-signout" class="beta-signout" role="menuitem" type="button"><i data-lucide="log-out"></i><span>Sign out</span></button></div></div>`;
  const oldHeader = document.querySelector("body > header");
  if (oldHeader) oldHeader.replaceWith(header);
  else document.body.prepend(header);
  const drawer = document.createElement("dialog");
  drawer.id = "beta-menu";
  drawer.setAttribute("aria-label", "Menu");
  drawer.innerHTML = `<div class="beta-drawer-heading"><img src="/brand-logo.png" width="100" height="35" alt="BETA"><button id="beta-menu-close" class="beta-icon-button" type="button" title="Close menu" aria-label="Close menu"><i data-lucide="x"></i></button></div><nav aria-label="全站导航"><a href="${calendar}/sso/start?next=%2Fcalendar%2F"><i data-lucide="calendar-days"></i><span>日历</span></a><a href="${account}/checkin"><i data-lucide="scan-line"></i><span>扫码签到</span></a></nav>`;
  document.body.append(drawer);
  const menu = header.querySelector("#beta-account-menu");
  const toggle = header.querySelector("#beta-account-button");
  const items = () => [...menu.querySelectorAll("a, button")].filter(item => !item.hidden);
  function closeAccount() { menu.hidden = true; toggle.setAttribute("aria-expanded", "false"); }
  header.querySelector("#beta-menu-open").addEventListener("click", () => { closeAccount(); drawer.showModal(); });
  drawer.querySelector("#beta-menu-close").addEventListener("click", () => drawer.close());
  drawer.addEventListener("click", event => {
    const bounds = drawer.getBoundingClientRect();
    if (event.clientX > bounds.right || event.clientY > bounds.bottom) drawer.close();
  });
  toggle.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    toggle.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) items()[0].focus();
  });
  toggle.addEventListener("keydown", event => {
    if (event.key === "ArrowDown") { event.preventDefault(); menu.hidden = false; toggle.setAttribute("aria-expanded", "true"); items()[0].focus(); }
  });
  menu.addEventListener("keydown", event => {
    const list = items(), index = list.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      list[(index + (event.key === "ArrowDown" ? 1 : -1) + list.length) % list.length].focus();
    }
    if (event.key === "Tab") closeAccount();
  });
  header.querySelector("#beta-signout").addEventListener("click", async () => {
    const signout = header.querySelector("#beta-signout");
    signout.disabled = true;
    try {
      await fetch("/api/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: {"X-CSRF-Token": state?.csrf_token || ""}
      });
    } finally {
      closeAccount();
      location.reload();
    }
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !menu.hidden) { closeAccount(); toggle.focus(); } });
  document.addEventListener("click", event => { if (!header.querySelector(".beta-account").contains(event.target)) closeAccount(); });

  let state = null;
  async function refresh() {
    const response = await fetch("/api/site/session", {credentials: "same-origin", cache: "no-store"});
    if (!response.ok) throw new Error("账户状态暂时不可用。");
    state = await response.json();
    const user = state.user;
    const avatar = user?.avatar || "/default-avatar.png";
    const safeAvatar = avatar.startsWith("https://avatars.githubusercontent.com/") ? avatar : "/default-avatar.png";
    header.querySelector("#beta-avatar").src = safeAvatar;
    header.querySelector("#beta-menu-avatar").src = safeAvatar;
    header.querySelector("#beta-name").textContent = user?.name || "Sign in";
    header.querySelector("#beta-signout").hidden = !user;
    const theme = user?.preferences?.theme || "system";
    document.body.dataset.theme = theme === "dark"
      || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
    header.querySelector("#beta-profile-link").href = user
      ? `${profile}/sso/start?next=${encodeURIComponent("/" + user.profile.slug)}`
      : `${account}/login`;
    if (state.continue_url && host === new URL(account).hostname) {
      location.replace(state.continue_url);
      return state;
    }
    document.dispatchEvent(new CustomEvent("beta-session", {detail: state}));
    return state;
  }
  window.BetaShell = {refresh, get state() { return state; }};
  window.BetaShell.ready = refresh().then(result => {
    if (host !== new URL(account).hostname && !result.user && !result.sso_checked && result.sso_available) {
      location.replace("/sso/start?next=" + encodeURIComponent(location.pathname + location.search));
    }
    return result;
  }).catch(() => {
    header.querySelector("#beta-name").textContent = "账户暂不可用";
    return null;
  });
  document.addEventListener("account-ready", () => refresh().catch(() => {}));
  document.addEventListener("account-closed", () => { if (state) refresh().catch(() => {}); });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh().catch(() => {});
  });
  window.addEventListener("pageshow", event => { if (event.persisted) refresh().catch(() => {}); });
  window.lucide?.createIcons();
})();
