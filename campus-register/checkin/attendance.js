import {joinToken, labels, model, prefilled, states} from "./context.js";
import {ensureFactor} from "./factor.js";
import {$, api, badge, busy, fieldLabel, icons, notice, stamp} from "./ui.js";

export function showReceipt(receipt) {
  $("#attend-form").hidden = true;
  $("#success").hidden = false;
  $("#unavailable").hidden = true;
  $("#receipt-time").textContent = `${stamp(receipt.checked_at)}（北京时间）`;
  $("#receipt-id").textContent = `签到编号 ${receipt.id}`;
  $("#attend-form").reset();
  for (const input of $("#answer-fields").querySelectorAll("input")) input.value = "";
  prefilled.clear();
}

export async function loadJoin() {
  const result = await api(`/api/checkin/join/${joinToken}`);
  model.joinEvent = result.event;
  document.title = `${model.joinEvent.title} · 活动签到`;
  $("#attend-title").textContent = model.joinEvent.title;
  $("#attend-location").textContent = model.joinEvent.location;
  $("#attend-time").textContent = `${stamp(model.joinEvent.starts_at)} — ${stamp(model.joinEvent.ends_at)}（北京时间）`;
  badge($("#attend-state"), model.joinEvent.status);
  $("#attend-purpose").textContent = model.joinEvent.purpose;
  $("#attend-organizer").textContent = model.joinEvent.organizer;
  $("#attend-retention").textContent = "长期保存，不自动删除；可联系管理员申请查阅、更正或删除";
  $("#document-warning").hidden = !model.joinEvent.fields.includes("document");
  if (result.receipt) { showReceipt(result.receipt); return; }
  $("#attend-form").hidden = model.joinEvent.status !== "open";
  if (model.joinEvent.status !== "open") {
    notice("#unavailable", states[model.joinEvent.status] + "，当前不能提交签到。");
    return;
  }
  if (!$("#answer-fields").children.length) {
    for (const field of model.joinEvent.fields) {
      if (field === "document") {
        const group = document.createElement("fieldset");
        group.className = "document-fields";
        const legend = document.createElement("legend");
        legend.textContent = labels.document;
        group.append(legend);
        for (const [name, title] of [["type", "证件类型"], ["country", "签发国家或地区代码"], ["number", "证件号码"]]) {
          const label = document.createElement("label");
          label.textContent = title;
          const input = document.createElement(name === "type" ? "select" : "input");
          input.name = "document_" + name;
          input.required = true;
          input.autocomplete = "off";
          if (name === "type") {
            for (const [value, text] of Object.entries(model.state.documents)) input.add(new Option(text, value));
          } else if (name === "country") {
            input.maxLength = 2;
            input.pattern = "[A-Za-z]{2}";
            input.value = "CN";
            input.placeholder = "CN";
          } else { input.maxLength = 24; }
          label.append(input);
          group.append(label);
        }
        $("#answer-fields").append(group);
        continue;
      }
      const label = document.createElement("label");
      label.textContent = fieldLabel(model.joinEvent, field);
      const input = document.createElement("input");
      input.name = field;
      input.required = true;
      input.autocomplete = {name: "name", email: "email", phone: "tel-national", student_id: "off"}[field] || "off";
      input.type = field === "email" ? "email" : field === "phone" ? "tel" : "text";
      input.maxLength = {name: 80, email: 254, phone: 11, student_id: 32}[field] || 500;
      if (field === "phone") { input.pattern = "1[3-9][0-9]{9}"; input.inputMode = "numeric"; }
      if (field === "student_id") input.pattern = "[A-Za-z0-9][A-Za-z0-9\\-]{2,31}";
      label.append(input);
      $("#answer-fields").append(label);
    }
  }
  $("#prefill").hidden = !model.state.user;
  $("#attend-login").hidden = !!model.state.user;
}

$("#prefill").addEventListener("click", () => busy($("#prefill"), async () => {
  const savedFields = model.joinEvent.fields.filter(f => labels[f]);
  if (!confirm(`仅为「${model.joinEvent.title}」读取已保存的${savedFields.map(f => labels[f]).join("、")}。不会自动提交，是否继续？`)) return;
  if (savedFields.some(f => ["phone", "document"].includes(f))) await ensureFactor();
  const grant = await api(`/api/checkin/join/${joinToken}/prefill/authorize`, {consent: true, policy: model.state.policy});
  const result = await api(`/api/checkin/join/${joinToken}/prefill`, {token: grant.token});
  if (document.hidden) return;
  for (const [field, value] of Object.entries(result.values)) {
    if (field === "document" && value) {
      for (const key of ["type", "country", "number"]) {
        $(`#answer-fields [name="document_${key}"]`).value = value[key];
      }
      prefilled.add(field);
      continue;
    }
    const input = $(`#answer-fields input[name="${field}"]`);
    if (input) { input.value = value; prefilled.add(field); }
  }
  notice("#message", "已读取本次需要的资料，请核对后确认签到；未保存的项目仍需填写。");
}));

$("#attend-form").addEventListener("submit", event => {
  event.preventDefault();
  busy($("#submit"), async () => {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    if (model.joinEvent.fields.includes("document")) {
      values.document = Object.fromEntries(["type", "country", "number"].map(key => [key, values["document_" + key]]));
      for (const key of ["type", "country", "number"]) delete values["document_" + key];
    }
    const result = await api(`/api/checkin/join/${joinToken}`, {values, consent: $("#consent").checked, policy: model.state.policy});
    showReceipt(result.receipt);
    notice("#message");
    icons();
  });
});
