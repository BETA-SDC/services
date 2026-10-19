const settingsPanels = {profile: "account-view", privacy: "private-page",
  security: "security-view", connections: "connections-view", preferences: "preferences-view"};
const settingsTitles = {profile: "个人资料", privacy: "隐私资料", security: "双重验证（2FA）",
  connections: "关联账户", preferences: "偏好设置"};
let selectedSettings = "profile";
let factorPending = null;
let factorBusy = false;
let factorChallenge = "";
let factorEmailWait = 0;
let factorEmailTimer;

function selectSettings(name, navigate = true) {
  selectedSettings = name in settingsPanels ? name : "profile";
  $("#public-settings").hidden = selectedSettings !== "profile";
  for (const [key, id] of Object.entries(settingsPanels)) $(`#${id}`).hidden = key !== selectedSettings;
  for (const link of document.querySelectorAll("[data-settings]")) {
    if (link.dataset.settings === selectedSettings) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (navigate) history.pushState({}, "", "/account#" + selectedSettings);
  document.title = `${settingsTitles[selectedSettings]} · BETA`;
  document.dispatchEvent(new CustomEvent("settings-panel", {detail: selectedSettings}));
}

for (const link of document.querySelectorAll("[data-settings]")) {
  link.addEventListener("click", event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    selectSettings(link.dataset.settings);
  });
}

document.addEventListener("account-ready", () => {
  let name = location.hash.slice(1);
  if (githubResult) name = "connections";
  if (sessionStorage.getItem("suggestPasskeySetup") === "1") {
    sessionStorage.removeItem("suggestPasskeySetup");
    name = "security";
    $("#passkey-setup-notice").hidden = false;
  }
  selectSettings(name || "profile", false);
  history.replaceState({}, "", "/account#" + selectedSettings);
});

$("#setup-notice-dismiss").addEventListener("click", () => { $("#passkey-setup-notice").hidden = true; });
window.addEventListener("hashchange", () => {
  if (currentUser) selectSettings(location.hash.slice(1), false);
});

async function refreshSecurity() {
  const state = await api("/api/security");
  $("#security-email").textContent = state.email;
  $("#passkey-count").textContent = state.passkey_count ? `已添加 ${state.passkey_count} 个通行密钥，可直接登录账户` : "尚未添加";
  $("#passkey-add").disabled = !window.PublicKeyCredential;
  $("#passkey-remove").hidden = !state.passkey_count;
  $("#security-passkey-verify").disabled = !state.passkey_count || !window.PublicKeyCredential;
  $("#factor-passkey").hidden = !state.passkey_count || !window.PublicKeyCredential;
  $("#factor-divider").hidden = $("#factor-passkey").hidden;
  $("#security-verification-state").textContent = state.verification
    ? `本次已通过${state.verification.method === "passkey" ? "通行密钥" : "邮箱验证码"}验证，有效至 ${new Date(state.verification.expires_at * 1000).toLocaleTimeString("zh-CN")}`
    : "当前会话尚未完成二次验证";
  return state;
}

function finishFactor(error = null) {
  if (!factorPending) return;
  const promise = factorPending;
  factorPending = null;
  if (!error) factorChallenge = "";
  $("#factor-code").value = "";
  if ($("#factor-dialog").open) $("#factor-dialog").close();
  if (error) promise.reject(error);
  else promise.resolve();
}

async function ensureSecondFactor({force = false} = {}) {
  const state = await refreshSecurity();
  if (!force && state.verification) return;
  if (factorPending) throw new Error("请先完成当前验证。");
  $("#factor-error").hidden = true;
  $("#factor-status").hidden = true;
  $("#factor-code-form").hidden = !factorChallenge;
  $("#factor-code").value = "";
  $("#factor-dialog").showModal();
  updateEmailCooldown();
  return new Promise((resolve, reject) => { factorPending = {resolve, reject}; });
}

$("#factor-close").addEventListener("click", () => finishFactor(new Error("已取消二次验证。")));
$("#factor-dialog").addEventListener("cancel", event => {
  event.preventDefault();
  finishFactor(new Error("已取消二次验证。"));
});
document.addEventListener("account-closed", () => {
  finishFactor(new Error("登录会话已结束。"));
  factorChallenge = "";
  factorEmailWait = 0;
  clearInterval(factorEmailTimer);
  $("#passkey-password").value = "";
  for (const id of ["security-status", "security-error", "passkey-setup-notice"]) $(`#${id}`).hidden = true;
});

$("#factor-passkey").addEventListener("click", async () => {
  if (factorBusy) return;
  factorBusy = true;
  $("#factor-passkey").disabled = true;
  showError("", "#factor-error");
  try {
    await webauthn("stepup", {consent: true});
    await refreshSecurity();
    finishFactor();
  } catch (error) {
    showError(error.name === "NotAllowedError" ? "设备验证已取消或不可用，可改用邮箱验证码。" : failureMessage(error), "#factor-error");
  } finally { factorBusy = false; $("#factor-passkey").disabled = false; }
});

function updateEmailCooldown() {
  const remaining = Math.max(0, Math.ceil((factorEmailWait - Date.now()) / 1000));
  $("#factor-email-send").disabled = factorBusy || remaining > 0;
  $("#factor-email-send span").textContent = remaining ? `${remaining} 秒后重新发送` : "发送邮箱验证码";
  if (!remaining) { clearInterval(factorEmailTimer); factorEmailTimer = null; }
}

$("#factor-email-send").addEventListener("click", async () => {
  if (factorBusy || Date.now() < factorEmailWait) return;
  factorBusy = true;
  showError("", "#factor-error");
  updateEmailCooldown();
  try {
    const state = await api("/api/security/email/send", {});
    factorChallenge = state.challenge;
    factorEmailWait = Date.now() + state.retry_after * 1000;
    $("#factor-code-form").hidden = false;
    $("#factor-status").textContent = state.message;
    $("#factor-status").hidden = false;
    $("#factor-code").focus();
    clearInterval(factorEmailTimer);
    factorEmailTimer = setInterval(updateEmailCooldown, 1000);
  } catch (error) { showError(failureMessage(error), "#factor-error"); }
  finally { factorBusy = false; updateEmailCooldown(); }
});

$("#factor-code-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (factorBusy) return;
  factorBusy = true;
  showError("", "#factor-error");
  try {
    await api("/api/security/email/verify", {challenge: factorChallenge, code: $("#factor-code").value.trim()});
    await refreshSecurity();
    finishFactor();
  } catch (error) { showError(failureMessage(error), "#factor-error"); }
  finally { factorBusy = false; }
});

async function verifyFromSecurity() {
  try {
    await ensureSecondFactor({force: true});
    $("#security-status").textContent = "二次验证已完成。";
    $("#security-status").hidden = false;
  } catch (error) { showError(failureMessage(error), "#security-error"); }
}
$("#security-email-verify").addEventListener("click", verifyFromSecurity);
$("#security-passkey-verify").addEventListener("click", verifyFromSecurity);

async function refreshPublicProfile() {
  const state = await api("/api/site/session");
  if (!state.user) return;
  const profile = state.user.profile;
  $("#my-public-profile").href = profile.url;
  $("#my-public-profile").textContent = profile.url;
  if (!$("#public-bio-input").matches(":focus") && !publicBioDirty) $("#public-bio-input").value = profile.bio;
  const labels = {pending: "等待管理员审核", approved: "已批准", rejected: "已驳回", superseded: "已更新申请"};
  $("#profile-address-state").textContent = profile.request
    ? `${profile.request.slug} · ${labels[profile.request.state] || profile.request.state}`
    : "默认使用注册编号；自定义地址经管理员批准后生效。";
}
document.addEventListener("account-ready", () => refreshPublicProfile().catch(error => showError(failureMessage(error), "#account-error")));
async function refreshPreferences() {
  const preferences = await api("/api/preferences");
  $("#global-theme").value = preferences.theme;
  applyGlobalTheme(preferences.theme);
}
function applyGlobalTheme(theme) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.body.dataset.theme = dark ? "dark" : "light";
}
document.addEventListener("account-ready", () => refreshPreferences().catch(error => showError(failureMessage(error), "#account-error")));
$("#preferences-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const result = await api("/api/preferences", {theme: $("#global-theme").value}, "PUT");
    applyGlobalTheme(result.theme);
    $("#account-status").textContent = "偏好设置已保存。";
    $("#account-status").hidden = false;
  } catch (error) { showError(failureMessage(error), "#account-error"); }
});
let publicBioDirty = false;
let studentVersion = 0;
function clearStudent() {
  studentVersion++;
  $("#profile-student").value = "";
  $("#profile-student").disabled = true;
  $("#student-save").disabled = true;
  $("#student-status").hidden = true;
  showError("", "#student-error");
}
document.addEventListener("account-ready", async () => {
  clearStudent();
  const version = studentVersion;
  try {
    const result = await api("/api/profile/student-id");
    if (version !== studentVersion || !currentUser) return;
    $("#profile-student").value = result.student_id || "";
    $("#profile-student").disabled = false;
    $("#student-save").disabled = false;
  } catch (error) {
    if (version === studentVersion) showError(failureMessage(error), "#student-error");
  }
});
document.addEventListener("account-closed", clearStudent);
$("#student-form").addEventListener("submit", async event => {
  event.preventDefault();
  if ($("#student-save").disabled || !currentUser) return;
  const version = studentVersion;
  $("#student-save").disabled = true;
  $("#profile-student").disabled = true;
  showError("", "#student-error");
  try {
    const result = await api("/api/profile/student-id", {student_id: $("#profile-student").value}, "PUT");
    if (version !== studentVersion || !currentUser) return;
    $("#profile-student").value = result.student_id || "";
    $("#student-status").textContent = result.student_id ? "学号已保存。" : "学号已清除。";
    $("#student-status").hidden = false;
  } catch (error) {
    if (version === studentVersion) showError(failureMessage(error), "#student-error");
  } finally {
    if (version === studentVersion && currentUser) {
      $("#profile-student").disabled = false;
      $("#student-save").disabled = false;
    }
  }
});
$("#public-bio-input").addEventListener("input", () => { publicBioDirty = true; });
document.addEventListener("account-closed", () => {
  publicBioDirty = false;
  $("#public-bio-input").value = "";
  $("#profile-address").value = "";
});
for (const [form, endpoint, value] of [
  ["#public-bio-form", "/api/profile/public", () => ({bio: $("#public-bio-input").value})],
  ["#profile-address-form", "/api/profile/address-request", () => ({slug: $("#profile-address").value.trim()})],
]) {
  $(form).addEventListener("submit", async event => {
    event.preventDefault();
    const button = $(form).querySelector("button");
    button.disabled = true;
    showError("", "#account-error");
    try {
      await api(endpoint, value());
      if (endpoint.endsWith("/public")) publicBioDirty = false;
      await refreshPublicProfile();
      $("#account-status").textContent = endpoint.endsWith("address-request") ? "主页地址申请已提交。" : "公开简介已保存。";
      $("#account-status").hidden = false;
    } catch (error) { showError(failureMessage(error), "#account-error"); }
    finally { button.disabled = false; }
  });
}
