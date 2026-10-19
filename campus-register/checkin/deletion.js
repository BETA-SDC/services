import {model, requestStates, requestsView} from "./context.js";
import {loadEvents, selectEvent} from "./events.js";
import {ensureFactor} from "./factor.js";
import {$, api, busy, cell, icons, notice} from "./ui.js";

$("#delete").addEventListener("click", () => busy($("#delete"), async () => {
  if (!confirm(`永久删除「${model.selected.title}」和全部签到记录？此操作无法撤销。`)) return;
  await ensureFactor();
  await api(`/api/checkin/events/${model.selected.id}`, {}, "DELETE");
  model.selected = null;
  $("#records-body").replaceChildren();
  $("#detail").hidden = true;
  await loadEvents();
  await loadRequests();
}));

$("#request-delete").addEventListener("click", () => {
  $("#deletion-form").reset();
  $("#deletion-event").textContent = model.selected.title;
  notice("#deletion-error");
  $("#deletion-dialog").showModal();
});

$("#deletion-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    await api(`/api/checkin/events/${model.selected.id}/deletion-request`, {reason: $("#deletion-reason").value});
    $("#deletion-dialog").close();
    await selectEvent(model.selected.id);
    notice("#message", "删除申请已提交，等待管理员审核。");
  }, "#deletion-error");
});

export async function loadRequests() {
  $("#requests-section").hidden = !model.state.can_manage || !requestsView;
  if (!model.state.can_manage || !requestsView) { $("#requests-body").replaceChildren(); return; }
  const result = await api(`/api/checkin/deletion-requests?page=${model.requestsPage}`);
  model.requestsTotal = result.total;
  $("#requests-page").textContent = `${model.requestsPage} / ${Math.max(1, Math.ceil(model.requestsTotal / 50))} 页 · 共 ${model.requestsTotal} 条`;
  $("#requests-previous").disabled = model.requestsPage <= 1;
  $("#requests-next").disabled = model.requestsPage * 50 >= model.requestsTotal;
  $("#requests-body").replaceChildren();
  $("#requests-empty").hidden = !!result.requests.length;
  $("#requests-table").hidden = !result.requests.length;
  for (const item of result.requests) {
    const row = document.createElement("tr");
    cell(row, item.event_title);
    cell(row, item.requester);
    cell(row, item.reason);
    cell(row, requestStates[item.status]);
    const action = cell(row, item.note || "");
    if (item.status === "pending") {
      const button = document.createElement("button");
      button.innerHTML = '<i data-lucide="clipboard-check"></i>审核';
      button.addEventListener("click", () => {
        model.reviewRequest = item;
        $("#review-form").reset();
        $("#review-summary").textContent = `${item.event_title} · ${item.requester}：${item.reason}`;
        notice("#review-error");
        $("#review-dialog").showModal();
      });
      action.append(button);
    }
    $("#requests-body").append(row);
  }
  icons();
}

$("#refresh-requests").addEventListener("click", () => busy($("#refresh-requests"), loadRequests));

$("#requests-previous").addEventListener("click", () => busy($("#requests-previous"), async () => {
  model.requestsPage--; await loadRequests();
}));

$("#requests-next").addEventListener("click", () => busy($("#requests-next"), async () => {
  model.requestsPage++; await loadRequests();
}));

$("#review-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const decision = event.submitter.value;
    const note = $("#review-note").value.trim();
    if (decision === "reject" && !note) throw new Error("请填写驳回原因。");
    await ensureFactor();
    await api(`/api/checkin/deletion-requests/${model.reviewRequest.id}/review`, {decision, note});
    $("#review-dialog").close();
    model.selected = null;
    $("#detail").hidden = true;
    $("#records-body").replaceChildren();
    await loadEvents();
    await loadRequests();
    notice("#message", decision === "approve" ? "已批准，签到表及全部签到记录已删除。" : "申请已驳回，签到表和记录保持不变。");
  }, "#review-error");
});
