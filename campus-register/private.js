$("#private-page").append($("#private-template").content.cloneNode(true));
const privateLabels = {phone: "电话号码", document: "证件信息"};
const allPrivateFields = Object.keys(privateLabels);
let privatePolicy = "";
let privateBusy = false;
let privateTimer;
let privateViewVersion = 0;
let privateData = null;
let privateDocuments = {};
let privateVisibleUntil = 0;

function clearPrivateView() {
  privateViewVersion += 1;
  clearTimeout(privateTimer);
  privateData = null;
  privateVisibleUntil = 0;
  $("#private-values").replaceChildren();
  $("#private-revealed").hidden = true;
  $("#private-form").hidden = true;
  $("#private-unlock").hidden = false;
  for (const id of ["private-phone", "private-document-number"]) {
    $(`#${id}`).value = "";
  }
}

async function loadPrivate() {
  try {
    const state = await api("/api/private");
    if (!currentUser) return;
    privatePolicy = state.policy;
    privateDocuments = state.documents;
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
  } catch (error) { showError(failureMessage(error), "#private-error"); }
}

function decodeCredential(value) {
  const text = value.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(text + "=".repeat((4 - text.length % 4) % 4)), c => c.charCodeAt(0));
}

function encodeCredential(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function webauthn(kind, payload) {
  const start = await api(`/api/passkeys/${kind}/options`, payload);
  const publicKey = start.options.publicKey;
  publicKey.challenge = decodeCredential(publicKey.challenge);
  for (const name of ["allowCredentials", "excludeCredentials"]) {
    if (publicKey[name]) publicKey[name] = publicKey[name].map(item => ({...item, id: decodeCredential(item.id)}));
  }
  if (publicKey.user) publicKey.user.id = decodeCredential(publicKey.user.id);
  const credential = kind === "register"
    ? await navigator.credentials.create({publicKey})
    : await navigator.credentials.get({publicKey});
  if (!credential) throw new Error("通行密钥验证已取消。");
  const result = {id: credential.id, rawId: encodeCredential(credential.rawId), type: credential.type,
    response: {clientDataJSON: encodeCredential(credential.response.clientDataJSON)},
    clientExtensionResults: credential.getClientExtensionResults()};
  if (kind === "register") {
    result.response.attestationObject = encodeCredential(credential.response.attestationObject);
    result.response.transports = credential.response.getTransports?.() || [];
  } else {
    result.response.authenticatorData = encodeCredential(credential.response.authenticatorData);
    result.response.signature = encodeCredential(credential.response.signature);
    result.response.userHandle = credential.response.userHandle ? encodeCredential(credential.response.userHandle) : null;
  }
  return api(`/api/passkeys/${kind}/complete`, {flow: start.flow, credential: result});
}

function displayPrivate(values, fresh = true) {
  if (fresh) privateVisibleUntil = Date.now() + 300000;
  if (Date.now() >= privateVisibleUntil) { clearPrivateView(); return; }
  privateData = values;
  $("#private-values").replaceChildren();
  for (const field of allPrivateFields) {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = privateLabels[field];
    const value = values[field];
    detail.textContent = field === "document" && value
      ? `${privateDocuments[value.type] || value.type} · ${value.country} · ${value.number}（${value.validation === "checksum" ? "格式及校验位通过" : "仅格式检查"}）`
      : value || "未填写";
    $("#private-values").append(term, detail);
  }
  $("#private-unlock").hidden = true;
  $("#private-form").hidden = true;
  $("#private-revealed").hidden = false;
  clearTimeout(privateTimer);
  privateTimer = setTimeout(clearPrivateView, privateVisibleUntil - Date.now());
}

async function privateAction(purpose) {
  if (privateBusy || !privatePolicy) return;
  const context = {purpose, fields: allPrivateFields, consent: true, policy: privatePolicy};
  const values = purpose === "edit" ? {
    phone: $("#private-phone").value,
    document: $("#private-document-number").value.trim() ? {
      type: $("#private-document-type").value, number: $("#private-document-number").value,
      country: $("#private-country").value,
    } : null,
  } : null;
  privateBusy = true;
  showError("", "#private-error");
  $("#private-status").hidden = true;
  try {
    await ensureSecondFactor();
    if (!currentUser || selectedSettings !== "privacy") return;
    const version = privateViewVersion;
    const grant = await api("/api/private/authorize", context);
    const result = await api(`/api/private/${purpose}`, {token: grant.token, ...(values ? {values} : {})});
    if (version !== privateViewVersion || document.hidden || !currentUser) { clearPrivateView(); return; }
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
  } finally { privateBusy = false; }
}

$("#private-unlock").addEventListener("submit", event => { event.preventDefault(); privateAction("read"); });
$("#private-hide").addEventListener("click", clearPrivateView);
$("#private-edit").addEventListener("click", () => {
  if (!privateData) return;
  $("#private-phone").value = privateData.phone || "";
  $("#private-document-type").value = privateData.document?.type || "cn_id";
  $("#private-country").value = privateData.document?.country || "CN";
  $("#private-document-number").value = privateData.document?.number || "";
  $("#private-revealed").hidden = true;
  $("#private-form").hidden = false;
});
$("#private-form").addEventListener("submit", event => { event.preventDefault(); privateAction("edit"); });
$("#private-cancel").addEventListener("click", () => {
  if (privateData) displayPrivate(privateData, false);
  else clearPrivateView();
});
$("#private-delete").addEventListener("click", () => {
  if (window.confirm("确定删除全部私密资料吗？本次只删除电话和证件资料，学号与账户仍保留。")) privateAction("delete");
});
document.addEventListener("account-ready", () => {
  clearPrivateView();
  loadPrivate();
  refreshSecurity().catch(error => showError(failureMessage(error), "#security-error"));
});
document.addEventListener("settings-panel", () => clearPrivateView());
document.addEventListener("account-closed", clearPrivateView);
document.addEventListener("visibilitychange", () => { if (document.hidden) clearPrivateView(); });
window.addEventListener("pagehide", clearPrivateView);
$("#logout").addEventListener("click", clearPrivateView);

async function managePasskey(remove = false) {
  if (privateBusy || !$("#passkey-form").reportValidity()) return;
  privateBusy = true;
  showError("", "#security-error");
  try {
    const payload = {password: $("#passkey-password").value, consent: true};
    if (remove) {
      if (!window.confirm("确定移除此账户的全部通行密钥吗？")) return;
    }
    await ensureSecondFactor();
    if (remove) await api("/api/passkeys/remove", payload);
    else await webauthn("register", payload);
    $("#security-status").textContent = remove ? "全部通行密钥已移除。" : "通行密钥已添加，可直接登录账户。";
    $("#security-status").hidden = false;
    $("#passkey-setup-notice").hidden = true;
    await refreshSecurity();
  } catch (error) {
    showError(error.name === "NotAllowedError" ? "设备操作已取消或不支持通行密钥。" : failureMessage(error), "#security-error");
  } finally { $("#passkey-password").value = ""; clearPrivateView(); privateBusy = false; }
}
$("#passkey-form").addEventListener("submit", event => { event.preventDefault(); managePasskey(); });
$("#passkey-remove").addEventListener("click", () => managePasskey(true));

$("#passkey-login").addEventListener("click", async () => {
  if (pending) return;
  pending = true;
  $("#passkey-login").disabled = true;
  showError("");
  try {
    const state = await webauthn("login", {});
    const accountState = await api("/api/session");
    githubAccount = accountState.github;
    githubEnabled = accountState.github_enabled;
    showAccount(state.user);
  } catch (error) {
    showError(error.name === "NotAllowedError" ? "通行密钥登录已取消或设备没有可用密钥。" : failureMessage(error));
  } finally { pending = false; $("#passkey-login").disabled = false; }
});
