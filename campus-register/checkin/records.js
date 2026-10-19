import {model, recordsId} from "./context.js";
import {ensureFactor} from "./factor.js";
import {clearSensitive} from "./sensitive.js";
import {$, accessStatus, answerText, api, busy, cell, notice, stamp} from "./ui.js";

export async function loadEntries() {
  if (!recordsId) return;
  let result;
  const read = () => api(`/api/checkin/events/${recordsId}/entries?page=${model.page}`, undefined, "GET", model.recordPAT ? {"X-Checkin-PAT": model.recordPAT} : {});
  try { result = await read(); }
  catch (error) {
    if (error.code !== "second_factor_required") { clearSensitive(); throw error; }
    try {
      await ensureFactor();
      if (!model.recordPAT) throw new Error("二次验证已完成，请重新输入邮件中的 PAT。");
      result = await read();
    } catch (failure) { clearSensitive(); throw failure; }
  }
  if (document.hidden) return;
  model.recordEvent = {can_export: result.can_export};
  clearTimeout(model.recordExpiryTimer);
  if (result.expires_at) model.recordExpiryTimer = setTimeout(() => {
    clearSensitive();
    notice("#pat-message", "PAT 已到期，已清空当前显示的资料。");
  }, Math.max(0, result.expires_at * 1000 - Date.now()));
  $("#record-section").hidden = false;
  $("#record-expiry").textContent = result.expires_at
    ? `隐私资料查看有效至 ${stamp(result.expires_at)}（北京时间）`
    : "当前显示基本名单；电话、证件及自定义条目需 PAT，导出另需具备导出权限的 PAT。";
  $("#legacy-notice").hidden = !result.legacy_restricted;
  $("#export").hidden = !result.can_export;
  model.total = result.total;
  $("#record-count").textContent = String(model.total);
  $("#records-head").replaceChildren();
  const header = document.createElement("tr");
  for (const label of ["服务器签到时间（北京时间）", ...result.fields.map(f => result.field_labels[f]), "提交方式"]) cell(header, label, true);
  $("#records-head").append(header);
  $("#records-body").replaceChildren();
  for (const item of result.entries) {
    const row = document.createElement("tr");
    cell(row, stamp(item.checked_at));
    for (const field of result.fields) cell(row, answerText(item.values[field]));
    cell(row, item.registered ? "已登录账号" : "手动填写");
    $("#records-body").append(row);
  }
  if (!result.entries.length) {
    const row = document.createElement("tr");
    cell(row, "暂无签到记录").colSpan = result.fields.length + 2;
    $("#records-body").append(row);
  }
  $("#page-label").textContent = `${model.page} / ${Math.max(1, Math.ceil(model.total / 50))} 页 · 共 ${model.total} 人`;
  $("#previous").disabled = model.page <= 1;
  $("#next").disabled = model.page * 50 >= model.total;
}

$("#refresh").addEventListener("click", () => busy($("#refresh"), loadEntries));

$("#previous").addEventListener("click", () => busy($("#previous"), async () => { model.page--; await loadEntries(); }));

$("#next").addEventListener("click", () => busy($("#next"), async () => { model.page++; await loadEntries(); }));

$("#export").addEventListener("click", () => busy($("#export"), async () => {
  await ensureFactor();
  if (!model.recordPAT || !model.recordEvent?.can_export) throw new Error("请重新验证具备导出权限的 PAT。");
  const response = await fetch(`/api/checkin/events/${recordsId}/entries?format=csv`, {
    cache: "no-store", credentials: "same-origin", headers: {"X-Checkin-PAT": model.recordPAT},
  });
  if (!response.ok) { clearSensitive(); throw new Error((await response.json()).error || "导出失败。"); }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `checkin-${recordsId}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}));

export async function loadAccessPage() {
  const result = await api(`/api/checkin/events/${recordsId}/access-requests`);
  $("#records-page").hidden = false;
  $("#records-title").textContent = result.title;
  $("#records-back").href = result.intro_path;
  document.title = `${result.title} · 签到名单`;
  const pending = result.requests.find(item => item.status === "pending");
  $("#access-request-form").hidden = !!pending;
  const latest = result.requests[0];
  $("#my-access-status").textContent = latest
    ? `最近申请：${accessStatus(latest)}${latest.expires_at ? " · 有效至 " + stamp(latest.expires_at) : ""}${latest.note ? " · " + latest.note : ""}。结果通过系统邮件送达。`
    : "基本名单可直接查看；隐私字段或导出需申请限时 PAT。";
  if (!model.recordPAT) await loadEntries();
}

$("#access-request-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    await api(`/api/checkin/events/${recordsId}/access-requests`, {reason: $("#access-reason").value});
    $("#access-request-form").reset();
    await loadAccessPage();
    notice("#pat-message", "申请已提交，系统邮件会通知管理员及申请人。");
  }, "#pat-error");
});

$("#records-factor").addEventListener("click", () => busy($("#records-factor"), async () => {
  await ensureFactor();
  notice("#pat-message", "二次验证已完成，请填写邮件中的 PAT。");
}, "#pat-error"));

$("#pat-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    model.recordPAT = $("#record-pat").value.trim();
    $("#record-pat").value = "";
    model.page = 1;
    await loadEntries();
  }, "#pat-error");
});

$("#basic-records").addEventListener("click", () => busy($("#basic-records"), async () => {
  clearSensitive();
  model.page = 1;
  await loadEntries();
}, "#pat-error"));
