import {detailPath, model} from "./context.js";
import {$, api, busy, icons, notice} from "./ui.js";

export async function loadPage(preserveDraft = false) {
  const draft = preserveDraft && model.pageData?.can_edit ? {
    description: $("#page-edit-description").value, visibility: $("#page-visibility").value,
  } : null;
  $("#event-page").hidden = true;
  $("#page-editor").hidden = true;
  model.pageData = null;
  const result = await api(detailPath.replace("/checkin/events/", "/api/checkin/pages/"));
  model.pageData = result;
  document.title = `${result.event.title} · 签到介绍`;
  $("#page-title").textContent = result.event.title;
  $("#page-organizer").textContent = result.event.organizer;
  $("#page-count").textContent = String(result.event.count);
  $("#page-description").textContent = result.event.description || "暂无活动介绍。";
  $("#page-editor").hidden = !result.can_edit;
  $("#page-manage").hidden = !result.can_edit;
  $("#page-back").hidden = !result.can_edit;
  $("#page-access").hidden = !result.records_path;
  if (result.records_path) $("#page-access").href = result.records_path;
  if (result.can_edit) {
    $("#page-manage").href = result.manage_path;
    $("#page-edit-description").value = draft ? draft.description : result.event.description;
    $("#page-visibility").value = draft ? draft.visibility : result.visibility;
  }
  $("#event-page").hidden = false;
  icons();
}

$("#page-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    notice("#page-save-message");
    await api(`/api/checkin/events/${model.pageData.event_id}/visibility`, {
      description: $("#page-edit-description").value, visibility: $("#page-visibility").value,
    }, "PUT");
    await loadPage();
    notice("#page-save-message", "已保存。");
  }, "#page-save-error");
});
