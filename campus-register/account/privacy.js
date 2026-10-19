import { api } from "./client.js";

import { $, allPrivateFields, model, privateLabels } from "./context.js";

import { ensureSecondFactor, refreshSecurity } from "./factor.js";

import { failureMessage, showError } from "./ui.js";

import { webauthn } from "./webauthn.js";

export function clearPrivateView() {
  model.privateViewVersion += 1;
  clearTimeout(model.privateTimer);
  model.privateData = null;
  model.privateVisibleUntil = 0;
  $("#private-values").replaceChildren();
  $("#private-revealed").hidden = true;
  $("#private-form").hidden = true;
  $("#private-unlock").hidden = false;
  for (const id of ["private-phone", "private-document-number"]) {
    $(`#${id}`).value = "";
  }
}

export async function loadPrivate() {
  try {
    const state = await api("/api/private");
    if (!model.currentUser) return;
    model.privatePolicy = state.policy;
    model.privateDocuments = state.documents;
    $("#private-summary").replaceChildren();
    for (const [field, label] of Object.entries(privateLabels)) {
      const term = document.createElement("dt");
      const value = document.createElement("dd");
      term.textContent = label;
      value.textContent = state.fields[field]?.saved ? "已加密保存" : "未填写";
      $("#private-summary").append(term, value);
    }
    $("#private-document-type").replaceChildren();
    for (const [type, name] of Object.entries(state.documents)) {
      const option = document.createElement("option");
      option.value = type;
      option.textContent = name;
      $("#private-document-type").append(option);
    }
  } catch (error) {
    showError(failureMessage(error), "#private-error");
  }
}

export function displayPrivate(values, fresh = true) {
  if (fresh) model.privateVisibleUntil = Date.now() + 300000;
  if (Date.now() >= model.privateVisibleUntil) {
    clearPrivateView();
    return;
  }
  model.privateData = values;
  $("#private-values").replaceChildren();
  for (const field of allPrivateFields) {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = privateLabels[field];
    const value = values[field];
    detail.textContent =
      field === "document" && value
        ? `${model.privateDocuments[value.type] || value.type} · ${value.country} · ${value.number}（${value.validation === "checksum" ? "格式及校验位通过" : "仅格式检查"}）`
        : value || "未填写";
    $("#private-values").append(term, detail);
  }
  $("#private-unlock").hidden = true;
  $("#private-form").hidden = true;
  $("#private-revealed").hidden = false;
  clearTimeout(model.privateTimer);
  model.privateTimer = setTimeout(
    clearPrivateView,
    model.privateVisibleUntil - Date.now(),
  );
}

export async function privateAction(purpose) {
  if (model.privateBusy || !model.privatePolicy) return;
  const context = {
    purpose,
    fields: allPrivateFields,
    consent: true,
    policy: model.privatePolicy,
  };
  const values =
    purpose === "edit"
      ? {
          phone: $("#private-phone").value,
          document: $("#private-document-number").value.trim()
            ? {
                type: $("#private-document-type").value,
                number: $("#private-document-number").value,
                country: $("#private-country").value,
              }
            : null,
        }
      : null;
  model.privateBusy = true;
  showError("", "#private-error");
  $("#private-status").hidden = true;
  try {
    await ensureSecondFactor();
    if (!model.currentUser || model.selectedSettings !== "privacy") return;
    const version = model.privateViewVersion;
    const grant = await api("/api/private/authorize", context);
    const result = await api(`/api/private/${purpose}`, {
      token: grant.token,
      ...(values ? { values } : {}),
    });
    if (
      version !== model.privateViewVersion ||
      document.hidden ||
      !model.currentUser
    ) {
      clearPrivateView();
      return;
    }
    if (purpose === "read" || purpose === "edit") {
      displayPrivate(result.values);
      if (purpose === "edit") {
        $("#private-status").textContent = "隐私资料已加密保存。";
        $("#private-status").hidden = false;
      }
    } else {
      clearPrivateView();
      $("#private-status").textContent = "全部私密资料已删除。";
      $("#private-status").hidden = false;
    }
    await loadPrivate();
  } catch (error) {
    if (error.code === "second_factor_required") clearPrivateView();
    showError(failureMessage(error), "#private-error");
  } finally {
    model.privateBusy = false;
  }
}

export async function managePasskey(remove = false) {
  if (model.privateBusy || !$("#passkey-form").reportValidity()) return;
  model.privateBusy = true;
  showError("", "#security-error");
  try {
    const payload = { password: $("#passkey-password").value, consent: true };
    if (remove) {
      if (!window.confirm("确定移除此账户的全部通行密钥吗？")) return;
    }
    await ensureSecondFactor();
    if (remove) await api("/api/passkeys/remove", payload);
    else await webauthn("register", payload);
    $("#security-status").textContent = remove
      ? "全部通行密钥已移除。"
      : "通行密钥已添加，可直接登录账户。";
    $("#security-status").hidden = false;
    $("#passkey-setup-notice").hidden = true;
    await refreshSecurity();
  } catch (error) {
    showError(
      error.name === "NotAllowedError"
        ? "设备操作已取消或不支持通行密钥。"
        : failureMessage(error),
      "#security-error",
    );
  } finally {
    $("#passkey-password").value = "";
    clearPrivateView();
    model.privateBusy = false;
  }
}
