import {model, requestStates, states} from "./context.js";
import {loadEntries} from "./records.js";
import {$, api, badge, busy, cell, icons, localTime, notice, stamp} from "./ui.js";

export async function loadEvents() {
  const result = await api("/api/checkin/events");
  $("#events-body").replaceChildren();
  $("#empty").hidden = result.events.length !== 0;
  $("#events-table").hidden = !result.events.length;
  for (const event of result.events) {
    const row = document.createElement("tr");
    const title = document.createElement("button");
    title.className = "event-title";
    title.textContent = event.title;
    title.addEventListener("click", () => busy(title, () => selectEvent(event.id)));
    cell(row, "").append(title);
    if (model.state.can_manage) {
      const by = document.createElement("p");
      by.className = "muted";
      by.textContent = `发起人：${event.organizer}`;
      row.firstChild.append(by);
    }
    const status = document.createElement("span");
    badge(status, event.status);
    cell(row, "").append(status);
    cell(row, `${stamp(event.starts_at)} — ${stamp(event.ends_at)}`);
    cell(row, String(event.count));
    const open = document.createElement("button");
    open.className = "icon-button";
    open.title = "查看签到";
    open.setAttribute("aria-label", `查看签到：${event.title}`);
    open.innerHTML = '<i data-lucide="chevron-right"></i>';
    open.addEventListener("click", () => busy(open, () => selectEvent(event.id)));
    cell(row, "").append(open);
    $("#events-body").append(row);
  }
  icons();
}

export async function selectEvent(id) {
  model.selected = (await api(`/api/checkin/events/${id}`)).event;
  model.page = 1;
  renderDetail();
  await loadEntries();
}

export function renderDetail() {
  $("#detail").hidden = false;
  $("#detail-title").textContent = model.selected.title;
  $("#detail-page-link").href = model.selected.detail_path;
  $("#records-page-link").href = `/checkin/records/${model.selected.id}`;
  badge($("#detail-state"), model.selected.status);
  $("#detail-meta").textContent = `${model.selected.location || "未设置地点"} · ${stamp(model.selected.starts_at)} — ${stamp(model.selected.ends_at)} · 长期保存，由管理员管理`;
  for (const id of ["toggle", "rotate", "delete"]) {
    $("#" + id).hidden = !model.selected.can_manage;
  }
  const deletion = model.selected.deletion_request;
  $("#request-delete").hidden = model.selected.can_manage || deletion?.status === "pending";
  notice("#deletion-state", deletion ? `删除申请：${requestStates[deletion.status]}${deletion.note ? " · " + deletion.note : ""}` : "");
  $("#detail-calendar").hidden = !model.selected.calendar_uid;
  $("#detail-calendar").replaceChildren();
  if (model.selected.calendar_uid) {
    const link = document.createElement("a");
    link.href = "https://calendar.betasdc.duckdns.org/";
    link.textContent = "已关联日历事件";
    $("#detail-calendar").append(link);
  }
  $("#toggle span").textContent = model.selected.status === "closed" ? "恢复签到" : "暂停签到";
  $("#toggle i, #toggle svg")?.setAttribute("data-lucide", model.selected.status === "closed" ? "play" : "pause");
  $("#toggle").disabled = model.selected.status === "ended";
  $("#record-count").textContent = String(model.selected.count);
  icons();
}

export function updateQR() {
  $("#qr-title").textContent = model.selected.title;
  $("#qr-count").textContent = `${model.selected.count} 人已签到 · ${states[model.selected.status]}`;
  $("#qr-time").textContent = `${stamp(model.selected.starts_at)} — ${stamp(model.selected.ends_at)}`;
  if ($("#join-url").value !== model.selected.join_url) {
    $("#qr-image").src = model.selected.qr_url + "?v=" + Date.now();
    $("#join-url").value = model.selected.join_url;
  }
  $("#download-qr").href = model.selected.qr_url;
}

export async function loadCalendarEvents() {
  const requestId = ++model.calendarRequest;
  const day = $("#calendar-date").value;
  model.calendarEvents = [];
  const picker = $("#calendar-event");
  picker.replaceChildren(new Option("不关联，手动填写", ""));
  if (!day) {
    picker.disabled = false;
    notice("#calendar-message", "请选择事件日期，或手动填写活动。");
    return;
  }
  picker.disabled = true;
  notice("#calendar-message", "正在读取日历…");
  try {
    const result = await api("/api/checkin/calendar-events?date=" + encodeURIComponent(day));
    if (requestId !== model.calendarRequest) return;
    model.calendarEvents = result.events;
    model.calendarEvents.sort((a, b) => a.start.localeCompare(b.start));
    for (const item of model.calendarEvents) {
      picker.add(new Option(`${item.start.slice(0, 10)} · ${item.title}${item.status === "tentative" ? "（待定）" : ""}`, item.uid));
    }
    notice("#calendar-message", model.calendarEvents.length ? "" : "当天没有日历事件，可手动填写。");
  } catch (error) {
    if (requestId === model.calendarRequest) notice("#calendar-message", error.message);
  } finally {
    if (requestId === model.calendarRequest) picker.disabled = false;
  }
}

$("#new-event").addEventListener("click", () => {
  $("#create-form").reset();
  $("#custom-fields").replaceChildren();
  $("#add-custom").disabled = false;
  $("#calendar-date").value = localTime(Date.now()).slice(0, 10);
  $("#starts-at").value = localTime(Date.now());
  $("#ends-at").value = localTime(Date.now() + 7200000);
  notice("#create-error");
  $("#create-dialog").showModal();
  loadCalendarEvents();
});

$("#calendar-date").addEventListener("change", loadCalendarEvents);

$("#create-dialog").addEventListener("close", () => {
  model.calendarRequest++;
});

$("#calendar-event").addEventListener("change", () => {
  const item = model.calendarEvents.find(value => value.uid === $("#calendar-event").value);
  notice("#calendar-message");
  if (!item) return;
  const form = $("#create-form");
  form.elements.title.value = item.title.slice(0, 120);
  form.elements.location.value = (item.location || "").slice(0, 160);
  if (!form.elements.purpose.value) form.elements.purpose.value = "记录本次活动出勤";
  const parse = value => Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(value) ? value + "T00:00:00+08:00" : value);
  const start = parse(item.start);
  const end = item.end ? parse(item.end) : start + (item.allDay ? 86400000 : 3600000);
  const now = Date.now();
  if (Number.isFinite(start) && Number.isFinite(end) && end > now && start <= now + 90 * 86400000
      && end > Math.max(start, now) && end - Math.max(start, now) <= 7 * 86400000) {
    $("#starts-at").value = localTime(Math.max(start, now));
    $("#ends-at").value = localTime(end);
  } else {
    $("#starts-at").value = localTime(now);
    $("#ends-at").value = localTime(now + 7200000);
    notice("#calendar-message", "原活动时间不在可签到范围内，请确认本次签到时段。");
  }
});

$("#add-custom").addEventListener("click", () => {
  if ($("#custom-fields").children.length >= 8) return;
  const row = document.createElement("div");
  row.className = "custom-field-row";
  const input = document.createElement("input");
  input.required = true;
  input.maxLength = 60;
  input.placeholder = "条目名称";
  input.setAttribute("aria-label", "自定义条目名称");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "icon-button";
  remove.title = "删除条目";
  remove.setAttribute("aria-label", "删除条目");
  remove.innerHTML = '<i data-lucide="trash-2"></i>';
  remove.addEventListener("click", () => { row.remove(); $("#add-custom").disabled = false; });
  row.append(input, remove);
  $("#custom-fields").append(row);
  $("#add-custom").disabled = $("#custom-fields").children.length >= 8;
  input.focus();
  icons();
});

$("#create-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const form = new FormData(event.currentTarget);
    const custom = [...$("#custom-fields").querySelectorAll("input")].map((input, index) => ({
      id: `custom_${index + 1}`, label: input.value,
    }));
    const result = await api("/api/checkin/events", {
      title: form.get("title"), location: form.get("location"), purpose: form.get("purpose"),
      fields: [...form.getAll("fields"), ...custom.map(item => item.id)],
      custom_fields: custom, calendar_uid: $("#calendar-event").value,
      description: form.get("description"), visibility: form.get("visibility"),
      starts_at: Date.parse(form.get("starts_at") + ":00+08:00") / 1000,
      ends_at: Date.parse(form.get("ends_at") + ":00+08:00") / 1000,
    });
    $("#create-dialog").close();
    await loadEvents();
    await selectEvent(result.event.id);
    $("#present").click();
  }, "#create-error");
});

$("#present").addEventListener("click", () => {
  $("#join-url").value = "";
  updateQR();
  $("#qr-dialog").showModal();
  clearInterval(model.qrTimer);
  model.qrTimer = setInterval(async () => {
    if (document.hidden || !model.selected) return;
    try {
      model.selected = (await api(`/api/checkin/events/${model.selected.id}`)).event;
      updateQR();
    } catch (error) { notice("#error", error.message); $("#qr-dialog").close(); }
  }, 15000);
});

$("#qr-dialog").addEventListener("close", () => {
  clearInterval(model.qrTimer);
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  if (model.selected) { renderDetail(); loadEntries().catch(error => notice("#error", error.message)); }
});

$("#fullscreen").addEventListener("click", () => busy($("#fullscreen"), async () => {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await $("#qr-dialog").requestFullscreen();
}));

$("#copy-url").addEventListener("click", () => busy($("#copy-url"), async () => {
  try { await navigator.clipboard.writeText(model.selected.join_url); }
  catch (_) { $("#join-url").focus(); $("#join-url").select(); throw new Error("无法自动复制，请从链接栏复制。"); }
  notice("#message", "签到链接已复制。");
}));

$("#toggle").addEventListener("click", () => busy($("#toggle"), async () => {
  model.selected = (await api(`/api/checkin/events/${model.selected.id}`, {closed: model.selected.status !== "closed"}, "PATCH")).event;
  renderDetail();
  await loadEvents();
}));

$("#rotate").addEventListener("click", () => busy($("#rotate"), async () => {
  if (!confirm("更换二维码后，旧二维码和链接立即失效。已有签到记录会保留。")) return;
  model.selected = (await api(`/api/checkin/events/${model.selected.id}`, {rotate: true}, "PATCH")).event;
  notice("#message", "已更换二维码，旧链接已失效。");
}));
