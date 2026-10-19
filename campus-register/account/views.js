import { api } from "./client.js";

import {
  $,
  fields,
  form,
  githubResult,
  model,
  privatePageRequested,
} from "./context.js";

import { renderGithub } from "./github.js";

import { failureMessage, resetPasswords, showError } from "./ui.js";

export function setMode(
  next,
  { navigate = true, preserveStatus = false } = {},
) {
  if (model.pending) return;
  model.mode = next;
  const isLogin = model.mode === "login";
  $("#passkey-login").hidden = !isLogin || !window.PublicKeyCredential;
  $("#passkey-registration-note").hidden = isLogin;
  if (navigate) history.pushState({}, "", isLogin ? "/login" : "/register");
  $("#auth-view").hidden = false;
  $("#account-view").hidden = true;
  $("#private-page").hidden = true;
  $("#settings-view").hidden = true;
  $("#auth-identity").hidden = false;
  $(".auth-shell").classList.remove("settings-shell");
  document.dispatchEvent(new Event("account-closed"));
  $("#verification-view").hidden = true;
  document.title = `${isLogin ? "登录" : "创建账户"} · BETA`;
  $("#page-heading").textContent = isLogin ? "欢迎回来" : "创建你的账户";
  $("#page-subtitle").textContent = isLogin
    ? "登录 BETA 账户中心。"
    : "一个账户，连接校园里的你我。";
  $("#submit-label").textContent = isLogin ? "登录" : "创建账户";
  $("#switch-label").textContent = isLogin ? "还没有账户？" : "已经有账户？";
  $("#switch-link").textContent = isLogin ? "创建账户" : "前往登录";
  $("#switch-link").href = isLogin ? "/register" : "/login";
  for (const id of [
    "real-name",
    "nickname",
    "student-id",
    "email",
    "email-code",
    "confirm",
  ]) {
    $(`#${id}-field`).hidden = isLogin;
    $(`#${id}`).disabled = isLogin;
    $(`#${id}`).required = !isLogin && id !== "nickname";
  }
  $("#identifier-field").hidden = !isLogin;
  $("#identifier").disabled = !isLogin;
  $("#identifier").required = isLogin;
  $("#password").minLength = isLogin ? 1 : 15;
  $("#password").autocomplete = isLogin ? "current-password" : "new-password";
  $("#password-hint").hidden = isLogin;
  $("#github-option").hidden = isLogin || !model.githubEnabled;
  $("#send-code").disabled =
    isLogin || model.codeSending || Date.now() < model.codeWaitUntil;
  for (const name of ["register", "login"]) {
    $(`#${name}-tab`).removeAttribute("aria-current");
  }
  $(`#${model.mode}-tab`).setAttribute("aria-current", "page");
  for (const input of form.querySelectorAll("input"))
    input.removeAttribute("aria-invalid");
  resetPasswords();
  showError("");
  if (!preserveStatus) $("#status").hidden = true;
}

export function showAccount(user, navigate = true, accountState = null) {
  model.currentUser = user;
  resetPasswords();
  $("#auth-view").hidden = true;
  $("#verification-view").hidden = true;
  $("#settings-view").hidden = false;
  $("#auth-identity").hidden = true;
  $(".auth-shell").classList.add("settings-shell");
  $("#account-name").textContent =
    user.nickname || user.real_name || user.username;
  $("#account-real-name").textContent = user.real_name || "待完善";
  $("#account-nickname").textContent = user.nickname || "未设置";
  $("#account-email").textContent = user.email;
  $("#profile-real-name").value = user.real_name || "";
  $("#profile-nickname").value = user.nickname || "";
  $("#profile-form").hidden = true;
  $("#profile-edit").hidden = false;
  $("#account-status").hidden = true;
  (accountState ? Promise.resolve(accountState) : api("/api/session"))
    .then((state) => {
      if (model.currentUser !== user) return;
      $("#admin-link").hidden = !state.access?.is_admin;
      $("#account-groups").textContent = state.access?.groups?.length
        ? `分组：${state.access.groups.map((group) => group.name).join("、")}`
        : "尚未加入分组";
      const sync = state.access?.organization_sync;
      const states = {
        member: "BETA-SDC 成员已确认",
        not_member: "未加入 BETA-SDC",
        public_member: "已加入 BETA-SDC，组织角色与 Team 待授权",
        unavailable: "组织同步失败",
        permission_required: "组织权限尚未确认，请重新授权",
      };
      $("#organization-sync-state").textContent = sync
        ? Date.now() / 1000 - sync.synced_at >= 86400
          ? "分组资料已过期，可重新同步"
          : states[sync.state] || ""
        : model.githubAccount
          ? "组织与 Team 尚未授权"
          : "";
      const owner = state.access?.github_owner;
      $("#owner-validity").hidden = !owner;
      $("#owner-validity").textContent = owner
        ? owner.expired
          ? "管理员身份已到期，请重新授权 GitHub"
          : `管理员身份有效至 ${new Date(owner.valid_until * 1000).toLocaleDateString("zh-CN")}`
        : "";
    })
    .catch(() => {
      $("#admin-link").hidden = true;
    });
  renderGithub();
  if (navigate) {
    const panel =
      location.pathname === "/account/privacy"
        ? "#privacy"
        : location.pathname === "/account"
          ? location.hash
          : "";
    history.replaceState({}, "", "/account" + panel);
  }
  document.dispatchEvent(new Event("account-ready"));
}

export async function initialize() {
  fields.disabled = true;
  $("#retry").hidden = true;
  $("#verification-retry").hidden = true;
  try {
    const state = await api("/api/session");
    model.githubEnabled = state.github_enabled;
    model.githubAccount = state.github;
    $("#github-option").hidden = model.mode === "login" || !model.githubEnabled;
    fields.disabled = false;
    showError("");
    if (location.pathname === "/verify")
      showVerification($("#verification-email").value, false);
    else if (state.user) {
      showAccount(state.user, true, state);
      if (githubResult) {
        const messages = {
          linked: "GitHub 账户已绑定。",
          cancelled: "已取消 GitHub 绑定。",
          invalid: "授权会话已失效，请重新发起绑定。",
          unavailable: "GitHub 暂时无法连接，请稍后重试。",
          conflict: "这个 GitHub 账户已绑定其他账户。",
          already: "请先解除原有绑定，再更换 GitHub 账户。",
        };
        $("#account-status").textContent =
          messages[githubResult] || "请重新发起 GitHub 绑定。";
        $("#account-status").hidden = false;
      }
    } else if (location.pathname === "/account" || privatePageRequested)
      setMode("login");
  } catch (error) {
    showError(
      failureMessage(error),
      location.pathname === "/verify" ? "#verification-error" : "#error",
    );
    $("#retry").hidden = false;
    $("#verification-retry").hidden = false;
  }
}

export function showVerification(email = "", navigate = true) {
  resetPasswords();
  showError("", "#verification-error");
  $("#verification-status").hidden = true;
  $("#auth-view").hidden = true;
  $("#account-view").hidden = true;
  $("#settings-view").hidden = true;
  $("#auth-identity").hidden = false;
  $(".auth-shell").classList.remove("settings-shell");
  $("#verification-view").hidden = false;
  $("#verification-email").value = email;
  $("#verification-heading").textContent = "验证你的邮箱";
  $("#verification-copy").textContent = model.verificationToken
    ? "点击下方按钮，确认验证此邮箱。"
    : "请检查收件箱和垃圾邮件。验证链接 30 分钟内有效。";
  $("#verify-confirm").hidden = !model.verificationToken;
  $("#verify-confirm").disabled = !model.csrfToken;
  $("#verify-resend").disabled = !model.csrfToken;
  $("#resend-form").hidden = Boolean(model.verificationToken);
  document.title = "邮箱验证 · BETA";
  if (navigate) history.pushState({}, "", "/verify");
}
