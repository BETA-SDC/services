import { api } from "./client.js";

import { $, model } from "./context.js";

import { refreshSecurity } from "./factor.js";

import {
  clearPrivateView,
  displayPrivate,
  loadPrivate,
  managePasskey,
  privateAction,
} from "./privacy.js";

import { failureMessage, showError } from "./ui.js";

import { showAccount } from "./views.js";

import { webauthn } from "./webauthn.js";

$("#private-unlock").addEventListener("submit", (event) => {
  event.preventDefault();
  privateAction("read");
});

$("#private-hide").addEventListener("click", clearPrivateView);

$("#private-edit").addEventListener("click", () => {
  if (!model.privateData) return;
  $("#private-phone").value = model.privateData.phone || "";
  $("#private-document-type").value =
    model.privateData.document?.type || "cn_id";
  $("#private-country").value = model.privateData.document?.country || "CN";
  $("#private-document-number").value =
    model.privateData.document?.number || "";
  $("#private-revealed").hidden = true;
  $("#private-form").hidden = false;
});

$("#private-form").addEventListener("submit", (event) => {
  event.preventDefault();
  privateAction("edit");
});

$("#private-cancel").addEventListener("click", () => {
  if (model.privateData) displayPrivate(model.privateData, false);
  else clearPrivateView();
});

$("#private-delete").addEventListener("click", () => {
  if (
    window.confirm(
      "确定删除全部私密资料吗？本次只删除电话和证件资料，学号与账户仍保留。",
    )
  )
    privateAction("delete");
});

document.addEventListener("account-ready", () => {
  clearPrivateView();
  loadPrivate();
  refreshSecurity().catch((error) =>
    showError(failureMessage(error), "#security-error"),
  );
});

document.addEventListener("settings-panel", () => clearPrivateView());

document.addEventListener("account-closed", clearPrivateView);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearPrivateView();
});

window.addEventListener("pagehide", clearPrivateView);

$("#logout").addEventListener("click", clearPrivateView);

$("#passkey-form").addEventListener("submit", (event) => {
  event.preventDefault();
  managePasskey();
});

$("#passkey-remove").addEventListener("click", () => managePasskey(true));

$("#passkey-login").addEventListener("click", async () => {
  if (model.pending) return;
  model.pending = true;
  $("#passkey-login").disabled = true;
  showError("");
  try {
    const state = await webauthn("login", {});
    const accountState = await api("/api/session");
    model.githubAccount = accountState.github;
    model.githubEnabled = accountState.github_enabled;
    showAccount(state.user, true, accountState);
  } catch (error) {
    showError(
      error.name === "NotAllowedError"
        ? "通行密钥登录已取消或设备没有可用密钥。"
        : failureMessage(error),
    );
  } finally {
    model.pending = false;
    $("#passkey-login").disabled = false;
  }
});
