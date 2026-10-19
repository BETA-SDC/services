import {loadAccessRequests} from "./approvals.js";
import {loadJoin} from "./attendance.js";
import {accessView, detailPath, joinToken, model, recordsId, requestsView} from "./context.js";
import {loadRequests} from "./deletion.js";
import {loadEvents, selectEvent} from "./events.js";
import {loadPage} from "./pages.js";
import {loadAccessPage, loadEntries} from "./records.js";
import {clearSensitive} from "./sensitive.js";
import {$, api, icons, notice} from "./ui.js";
import "./factor.js";

document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => {
  document.getElementById(button.dataset.close).close();
}));

document.addEventListener("visibilitychange", async () => {
  if (document.hidden) { clearSensitive(); return; }
  try {
    model.state = await api("/api/checkin/session");
    if (detailPath) await loadPage(true);
    else if (recordsId) { clearSensitive(); await loadAccessPage(); }
    else if (joinToken) await loadJoin();
    else if (model.selected && model.state.can_manage) await loadEntries();
    else if (!joinToken) await start();
  } catch (error) { notice("#error", error.message); }
});

window.addEventListener("pagehide", clearSensitive);

export async function start() {
  try {
    model.state = await api("/api/checkin/session");
    $("#loading").hidden = true;
    if (detailPath) { await loadPage(); }
    else if (recordsId) { await loadAccessPage(); }
    else if (joinToken) { $("#attend").hidden = false; await loadJoin(); }
    else {
      $("#manage").hidden = false;
      $("#new-event").hidden = !model.state.can_create || requestsView || accessView;
      $("#manage-login").hidden = !!model.state.user;
      $("#management-layout").hidden = !model.state.can_create;
      $("#events-section").hidden = !model.state.can_create || requestsView || accessView;
      $("#requests-nav").hidden = !model.state.can_manage;
      $("#access-nav").hidden = !model.state.can_manage;
      $("#events-nav span").textContent = model.state.can_manage ? "全部签到表" : "我创建的签到表";
      for (const id of ["events-nav", "requests-nav", "access-nav"]) $("#" + id).removeAttribute("aria-current");
      $(accessView ? "#access-nav" : requestsView ? "#requests-nav" : "#events-nav").setAttribute("aria-current", "page");
      $("#events-title").textContent = model.state.can_manage ? "全部签到表" : "我创建的签到表";
      if (!model.state.can_create) { model.selected = null; $("#detail").hidden = true; }
      notice("#access-note", model.state.can_create ? "" : model.state.user
        ? "发起签到需要有效的 BETA-SDC 成员认证。请在账户设置中核对 GitHub 组织信息。"
        : "请登录 BETA-SDC 成员账户。参加签到请打开活动二维码对应的链接。");
      if (model.state.can_create && !requestsView && !accessView) {
        await loadEvents();
        const eventId = new URLSearchParams(location.search).get("event");
        if (eventId && /^[a-f0-9]{32}$/.test(eventId)) await selectEvent(eventId);
      }
      if (requestsView) await loadRequests();
      if (accessView) await loadAccessRequests();
    }
    icons();
  } catch (error) { $("#loading").hidden = true; notice("#error", error.message); }
}

start();
