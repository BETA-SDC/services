import { api } from "./client.js";

import { $, model } from "./context.js";

import { failureMessage, showError } from "./ui.js";

export async function refreshSecurity() {
  const state = await api("/api/security");
  $("#security-email").textContent = state.email;
  $("#passkey-count").textContent = state.passkey_count
    ? `已添加 ${state.passkey_count} 个通行密钥，可直接登录账户`
    : "尚未添加";
  $("#passkey-add").disabled = !window.PublicKeyCredential;
  $("#passkey-remove").hidden = !state.passkey_count;
  $("#security-passkey-verify").disabled =
    !state.passkey_count || !window.PublicKeyCredential;
  $("#factor-passkey").hidden =
    !state.passkey_count || !window.PublicKeyCredential;
  $("#factor-divider").hidden = $("#factor-passkey").hidden;
  $("#security-verification-state").textContent = state.verification
    ? `本次已通过${state.verification.method === "passkey" ? "通行密钥" : "邮箱验证码"}验证，有效至 ${new Date(state.verification.expires_at * 1000).toLocaleTimeString("zh-CN")}`
    : "当前会话尚未完成二次验证";
  return state;
}

export function finishFactor(error = null) {
  if (!model.factorPending) return;
  const promise = model.factorPending;
  model.factorPending = null;
  if (!error) model.factorChallenge = "";
  $("#factor-code").value = "";
  if ($("#factor-dialog").open) $("#factor-dialog").close();
  if (error) promise.reject(error);
  else promise.resolve();
}

export async function ensureSecondFactor({ force = false } = {}) {
  const state = await refreshSecurity();
  if (!force && state.verification) return;
  if (model.factorPending) throw new Error("请先完成当前验证。");
  $("#factor-error").hidden = true;
  $("#factor-status").hidden = true;
  $("#factor-code-form").hidden = !model.factorChallenge;
  $("#factor-code").value = "";
  $("#factor-dialog").showModal();
  updateEmailCooldown();
  return new Promise((resolve, reject) => {
    model.factorPending = { resolve, reject };
  });
}

export function updateEmailCooldown() {
  const remaining = Math.max(
    0,
    Math.ceil((model.factorEmailWait - Date.now()) / 1000),
  );
  $("#factor-email-send").disabled = model.factorBusy || remaining > 0;
  $("#factor-email-send span").textContent = remaining
    ? `${remaining} 秒后重新发送`
    : "发送邮箱验证码";
  if (!remaining) {
    clearInterval(model.factorEmailTimer);
    model.factorEmailTimer = null;
  }
}

export async function verifyFromSecurity() {
  try {
    await ensureSecondFactor({ force: true });
    $("#security-status").textContent = "二次验证已完成。";
    $("#security-status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#security-error");
  }
}
