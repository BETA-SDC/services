import {accessView, model} from "./context.js";
import {ensureFactor} from "./factor.js";
import {$, accessStatus, api, busy, cell, icons, localTime, notice, stamp} from "./ui.js";

export async function loadAccessRequests() {
  $("#access-requests-section").hidden = !model.state.can_manage || !accessView;
  if (!model.state.can_manage || !accessView) return;
  const result = await api(`/api/checkin/access-requests?page=${model.accessPage}`);
  model.accessTotal = result.total;
  $("#access-page").textContent = `${model.accessPage} / ${Math.max(1, Math.ceil(model.accessTotal / 50))} 页 · 共 ${model.accessTotal} 条`;
  $("#access-previous").disabled = model.accessPage <= 1;
  $("#access-next").disabled = model.accessPage * 50 >= model.accessTotal;
  $("#access-empty").hidden = !!result.requests.length;
  $("#access-table").hidden = !result.requests.length;
  $("#access-body").replaceChildren();
  for (const item of result.requests) {
    const row = document.createElement("tr");
    cell(row, `${item.event_title} · ${item.applicant || "未填写姓名"}`);
    cell(row, item.reason);
    cell(row, accessStatus(item));
    cell(row, item.expires_at ? `${stamp(item.expires_at)} · ${item.mail_failed ? "邮件失败" : item.mailed_at ? "已交邮件服务器" : "待发送"}` : item.note);
    const actions = cell(row, "");
    if (item.status === "pending") {
      const button = document.createElement("button");
      button.innerHTML = '<i data-lucide="clipboard-check"></i>审核';
      button.addEventListener("click", () => {
        model.accessReview = item;
        $("#access-review-form").reset();
        $("#access-expires").value = localTime(Date.now() + 3600000);
        $("#access-review-summary").textContent = `${item.event_title} · ${item.applicant}：${item.reason}`;
        notice("#access-review-error");
        $("#access-review-dialog").showModal();
      });
      actions.append(button);
    } else if (item.status === "approved" && !item.revoked_at && item.expires_at > Date.now() / 1000) {
      for (const [action, title, icon] of [["resend", "重新邮件发送", "mail"], ["revoke", "撤销 PAT", "key-round"]]) {
        const button = document.createElement("button");
        button.className = "icon-button";
        button.title = title;
        button.setAttribute("aria-label", title);
        button.innerHTML = `<i data-lucide="${icon}"></i>`;
        button.addEventListener("click", () => busy(button, async () => {
          const note = action === "revoke" ? prompt("请填写撤销原因：") : "";
          if (note === null) return;
          await ensureFactor();
          await api(`/api/checkin/access-requests/${item.id}/${action}`, {note});
          await loadAccessRequests();
          notice("#message", action === "resend" ? "新 PAT 已加入系统邮件队列，旧 PAT 已失效。" : "PAT 已撤销，将通过系统邮件通知。");
        }));
        actions.append(button);
      }
    }
    $("#access-body").append(row);
  }
  icons();
}

$("#refresh-access").addEventListener("click", () => busy($("#refresh-access"), loadAccessRequests));

$("#access-previous").addEventListener("click", () => busy($("#access-previous"), async () => { model.accessPage--; await loadAccessRequests(); }));

$("#access-next").addEventListener("click", () => busy($("#access-next"), async () => { model.accessPage++; await loadAccessRequests(); }));

$("#access-review-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const decision = event.submitter.value;
    await ensureFactor();
    await api(`/api/checkin/access-requests/${model.accessReview.id}/review`, {
      decision, note: $("#access-review-note").value,
      expires_at: decision === "approve" ? Date.parse($("#access-expires").value + ":00+08:00") / 1000 : null,
      allow_export: decision === "approve" && $("#access-export").checked,
    });
    $("#access-review-dialog").close();
    await loadAccessRequests();
    notice("#message", decision === "approve" ? "已批准，限时 PAT 将通过系统邮件发送。" : "已驳回，将通过系统邮件通知申请人。");
  }, "#access-review-error");
});
