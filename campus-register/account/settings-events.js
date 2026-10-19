import { api } from "./client.js";

import { $, form, githubResult, model } from "./context.js";

import {
  finishFactor,
  refreshSecurity,
  updateEmailCooldown,
  verifyFromSecurity,
} from "./factor.js";

import { applyGlobalTheme, refreshPreferences } from "./preferences.js";

import { clearStudent, refreshPublicProfile } from "./profile.js";

import { selectSettings } from "./settings.js";

import { failureMessage, showError } from "./ui.js";

import { webauthn } from "./webauthn.js";

for (const link of document.querySelectorAll("[data-settings]")) {
  link.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return;
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
  history.replaceState({}, "", "/account#" + model.selectedSettings);
});

$("#setup-notice-dismiss").addEventListener("click", () => {
  $("#passkey-setup-notice").hidden = true;
});

window.addEventListener("hashchange", () => {
  if (model.currentUser) selectSettings(location.hash.slice(1), false);
});

$("#factor-close").addEventListener("click", () =>
  finishFactor(new Error("已取消二次验证。")),
);

$("#factor-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  finishFactor(new Error("已取消二次验证。"));
});

document.addEventListener("account-closed", () => {
  finishFactor(new Error("登录会话已结束。"));
  model.factorChallenge = "";
  model.factorEmailWait = 0;
  clearInterval(model.factorEmailTimer);
  $("#passkey-password").value = "";
  for (const id of [
    "security-status",
    "security-error",
    "passkey-setup-notice",
  ])
    $(`#${id}`).hidden = true;
});

$("#factor-passkey").addEventListener("click", async () => {
  if (model.factorBusy) return;
  model.factorBusy = true;
  $("#factor-passkey").disabled = true;
  showError("", "#factor-error");
  try {
    await webauthn("stepup", { consent: true });
    await refreshSecurity();
    finishFactor();
  } catch (error) {
    showError(
      error.name === "NotAllowedError"
        ? "设备验证已取消或不可用，可改用邮箱验证码。"
        : failureMessage(error),
      "#factor-error",
    );
  } finally {
    model.factorBusy = false;
    $("#factor-passkey").disabled = false;
  }
});

$("#factor-email-send").addEventListener("click", async () => {
  if (model.factorBusy || Date.now() < model.factorEmailWait) return;
  model.factorBusy = true;
  showError("", "#factor-error");
  updateEmailCooldown();
  try {
    const state = await api("/api/security/email/send", {});
    model.factorChallenge = state.challenge;
    model.factorEmailWait = Date.now() + state.retry_after * 1000;
    $("#factor-code-form").hidden = false;
    $("#factor-status").textContent = state.message;
    $("#factor-status").hidden = false;
    $("#factor-code").focus();
    clearInterval(model.factorEmailTimer);
    model.factorEmailTimer = setInterval(updateEmailCooldown, 1000);
  } catch (error) {
    showError(failureMessage(error), "#factor-error");
  } finally {
    model.factorBusy = false;
    updateEmailCooldown();
  }
});

$("#factor-code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (model.factorBusy) return;
  model.factorBusy = true;
  showError("", "#factor-error");
  try {
    await api("/api/security/email/verify", {
      challenge: model.factorChallenge,
      code: $("#factor-code").value.trim(),
    });
    await refreshSecurity();
    finishFactor();
  } catch (error) {
    showError(failureMessage(error), "#factor-error");
  } finally {
    model.factorBusy = false;
  }
});

$("#security-email-verify").addEventListener("click", verifyFromSecurity);

$("#security-passkey-verify").addEventListener("click", verifyFromSecurity);

document.addEventListener("account-ready", () =>
  refreshPublicProfile().catch((error) =>
    showError(failureMessage(error), "#account-error"),
  ),
);

document.addEventListener("account-ready", () =>
  refreshPreferences().catch((error) =>
    showError(failureMessage(error), "#account-error"),
  ),
);

$("#preferences-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api(
      "/api/preferences",
      { theme: $("#global-theme").value },
      "PUT",
    );
    applyGlobalTheme(result.theme);
    $("#account-status").textContent = "偏好设置已保存。";
    $("#account-status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#account-error");
  }
});

document.addEventListener("account-ready", async () => {
  clearStudent();
  const version = model.studentVersion;
  try {
    const result = await api("/api/profile/student-id");
    if (version !== model.studentVersion || !model.currentUser) return;
    $("#profile-student").value = result.student_id || "";
    $("#profile-student").disabled = false;
    $("#student-save").disabled = false;
  } catch (error) {
    if (version === model.studentVersion)
      showError(failureMessage(error), "#student-error");
  }
});

document.addEventListener("account-closed", clearStudent);

$("#student-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("#student-save").disabled || !model.currentUser) return;
  const version = model.studentVersion;
  $("#student-save").disabled = true;
  $("#profile-student").disabled = true;
  showError("", "#student-error");
  try {
    const result = await api(
      "/api/profile/student-id",
      { student_id: $("#profile-student").value },
      "PUT",
    );
    if (version !== model.studentVersion || !model.currentUser) return;
    $("#profile-student").value = result.student_id || "";
    $("#student-status").textContent = result.student_id
      ? "学号已保存。"
      : "学号已清除。";
    $("#student-status").hidden = false;
  } catch (error) {
    if (version === model.studentVersion)
      showError(failureMessage(error), "#student-error");
  } finally {
    if (version === model.studentVersion && model.currentUser) {
      $("#profile-student").disabled = false;
      $("#student-save").disabled = false;
    }
  }
});

$("#public-bio-input").addEventListener("input", () => {
  model.publicBioDirty = true;
});

document.addEventListener("account-closed", () => {
  model.publicBioDirty = false;
  $("#public-bio-input").value = "";
  $("#profile-address").value = "";
});

for (const [form, endpoint, value] of [
  [
    "#public-bio-form",
    "/api/profile/public",
    () => ({ bio: $("#public-bio-input").value }),
  ],
  [
    "#profile-address-form",
    "/api/profile/address-request",
    () => ({ slug: $("#profile-address").value.trim() }),
  ],
]) {
  $(form).addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $(form).querySelector("button");
    button.disabled = true;
    showError("", "#account-error");
    try {
      await api(endpoint, value());
      if (endpoint.endsWith("/public")) model.publicBioDirty = false;
      await refreshPublicProfile();
      $("#account-status").textContent = endpoint.endsWith("address-request")
        ? "主页地址申请已提交。"
        : "公开简介已保存。";
      $("#account-status").hidden = false;
    } catch (error) {
      showError(failureMessage(error), "#account-error");
    } finally {
      button.disabled = false;
    }
  });
}
