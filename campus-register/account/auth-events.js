import { api } from "./client.js";

import {
  $,
  defaultAvatarUrl,
  emailDomain,
  fields,
  form,
  model,
} from "./context.js";

import { bindGithub, renderGithub } from "./github.js";

import { renderCodeWait } from "./signup.js";

import { failureMessage, renderIcons, showError } from "./ui.js";

import { initialize, setMode, showAccount, showVerification } from "./views.js";

$("#email").addEventListener("input", () => {
  $("#email-code").value = "";
  const input = $("#email");
  let local = input.value.trim();
  if (
    local.toLowerCase().endsWith(emailDomain) &&
    local.indexOf("@") === local.lastIndexOf("@")
  ) {
    local = local.slice(0, -emailDomain.length);
    input.value = local;
  }
  const valid =
    /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$/.test(local) &&
    !local.includes("..") &&
    !local.startsWith(".") &&
    !local.endsWith(".");
  input.setCustomValidity(
    !local || valid
      ? ""
      : "请填写有效的邮箱用户名，仅支持 @westlake.edu.cn 邮箱。",
  );
});

for (const link of [
  $("#register-tab"),
  $("#login-tab"),
  $("#switch-link"),
  $("#verification-login"),
]) {
  link.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return;
    event.preventDefault();
    if (model.pending) return;
    setMode(link.getAttribute("href") === "/login" ? "login" : "register");
  });
}

for (const toggle of document.querySelectorAll("[data-toggle-password]")) {
  toggle.addEventListener("click", () => {
    const input = $(`#${toggle.dataset.togglePassword}`);
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(reveal));
    const suffix = input.id === "confirm" ? "确认密码" : "密码";
    toggle.setAttribute("aria-label", (reveal ? "隐藏" : "显示") + suffix);
    toggle.title = toggle.getAttribute("aria-label");
    toggle.innerHTML = `<i data-lucide="${reveal ? "eye-off" : "eye"}" aria-hidden="true"></i>`;
    renderIcons();
  });
}

$("#password").addEventListener("keyup", (event) => {
  $("#caps-lock").hidden = !event.getModifierState("CapsLock");
});

$("#password").addEventListener("blur", () => {
  $("#caps-lock").hidden = true;
});

$("#confirm").addEventListener("input", () => {
  $("#confirm").setCustomValidity("");
  $("#confirm").removeAttribute("aria-invalid");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (model.pending || !model.csrfToken) return;
  if (
    model.mode === "register" &&
    $("#password").value !== $("#confirm").value
  ) {
    $("#confirm").setAttribute("aria-invalid", "true");
    showError("两次输入的密码不一致。");
    $("#confirm").focus();
    return;
  }
  model.pending = true;
  fields.disabled = true;
  showError("");
  const action = model.mode;
  $("#submit-label").textContent =
    action === "login" ? "正在登录…" : "正在创建…";
  const password = $("#password").value;
  try {
    if (action === "register") {
      const account = await api("/api/register", {
        real_name: $("#real-name").value.trim(),
        nickname: $("#nickname").value.trim(),
        student_id: $("#student-id").value.trim(),
        email: $("#email").value.trim() + emailDomain,
        password,
        email_code: $("#email-code").value.trim(),
      });
      const bindGithub =
        model.githubEnabled && $("#github-after-register").checked;
      model.pending = false;
      $("#identifier").value = account.email;
      setMode("login");
      $("#status").textContent =
        "邮箱已验证，账户创建成功。请登录后添加通行密钥，建议使用指纹或面容验证。";
      $("#status").hidden = false;
      sessionStorage.setItem("bindGithubAfterLogin", bindGithub ? "1" : "0");
      sessionStorage.setItem("suggestPasskeySetup", "1");
    } else {
      const state = await api("/api/login", {
        identifier: $("#identifier").value.trim(),
        password,
      });
      const accountState = await api("/api/session");
      model.githubAccount = accountState.github;
      model.githubEnabled = accountState.github_enabled;
      showAccount(state.user, true, accountState);
      $("#logout").focus();
      if (sessionStorage.getItem("bindGithubAfterLogin") === "1") {
        sessionStorage.removeItem("bindGithubAfterLogin");
        model.pending = false;
        await bindGithub();
      }
    }
  } catch (error) {
    if (error.code === "email_verification_required") {
      model.verificationToken = null;
      model.pending = false;
      showVerification(error.email);
    } else showError(failureMessage(error));
  } finally {
    model.pending = false;
    fields.disabled = !model.csrfToken;
    $("#submit-label").textContent =
      model.mode === "login" ? "登录" : "创建账户";
  }
});

$("#profile-edit").addEventListener("click", () => {
  $("#profile-form").hidden = false;
  $("#profile-edit").hidden = true;
  $("#profile-real-name").focus();
});

$("#profile-cancel").addEventListener("click", () => {
  $("#profile-form").hidden = true;
  $("#profile-edit").hidden = false;
  $("#profile-real-name").value = model.currentUser?.real_name || "";
  $("#profile-nickname").value = model.currentUser?.nickname || "";
});

$("#profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (model.pending || !model.csrfToken) return;
  model.pending = true;
  const save = $("#profile-form button[type=submit]");
  save.disabled = true;
  showError("", "#account-error");
  try {
    const state = await api("/api/profile", {
      real_name: $("#profile-real-name").value.trim(),
      nickname: $("#profile-nickname").value.trim(),
    });
    showAccount(state.user, false);
    $("#account-status").textContent = "资料已保存。";
    $("#account-status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#account-error");
  } finally {
    model.pending = false;
    save.disabled = false;
  }
});

$("#account-avatar").addEventListener("error", () => {
  const avatar = $("#account-avatar");
  if (avatar.getAttribute("src") === defaultAvatarUrl) return;
  avatar.classList.add("default-avatar");
  avatar.alt = "默认头像";
  avatar.src = defaultAvatarUrl;
});

$("#github-avatar").addEventListener("load", () => {
  $("#github-avatar").hidden = false;
  $("#github-avatar-fallback").hidden = true;
});

$("#github-avatar").addEventListener("error", () => {
  $("#github-avatar").hidden = true;
  $("#github-avatar-fallback").hidden = false;
});

$("#github-link").addEventListener("click", bindGithub);

$("#github-authorize").addEventListener("click", bindGithub);

$("#github-refresh").addEventListener("click", async () => {
  if (model.pending || !model.csrfToken) return;
  model.pending = true;
  $("#github-refresh").disabled = true;
  showError("", "#account-error");
  try {
    const response = await api("/api/github/refresh", {});
    model.githubAccount = response.github;
    renderGithub();
    $("#account-status").textContent = "GitHub 资料已更新。";
    $("#account-status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#account-error");
  } finally {
    model.pending = false;
    $("#github-refresh").disabled = false;
  }
});

$("#github-unlink").addEventListener("click", async () => {
  if (model.pending || !model.csrfToken) return;
  model.pending = true;
  $("#github-unlink").disabled = true;
  showError("", "#account-error");
  try {
    const response = await api("/api/github/unlink", {});
    model.githubAccount = response.github;
    renderGithub();
    $("#account-status").textContent = "已解除 GitHub 绑定。";
    $("#account-status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#account-error");
    if (!model.csrfToken) document.dispatchEvent(new Event("account-retry"));
  } finally {
    model.pending = false;
    $("#github-unlink").disabled = false;
  }
});

setInterval(renderCodeWait, 1000);

$("#send-code").addEventListener("click", async () => {
  if (
    model.pending ||
    model.codeSending ||
    !model.csrfToken ||
    Date.now() < model.codeWaitUntil
  )
    return;
  if (!$("#email").reportValidity()) return;
  model.codeSending = true;
  renderCodeWait();
  showError("");
  try {
    const response = await api("/api/signup/code", {
      email: $("#email").value.trim() + emailDomain,
    });
    model.codeWaitUntil = Date.now() + response.retry_after * 1000;
    $("#code-status").textContent = response.message;
    $("#code-status").hidden = false;
    $("#email-code").focus();
  } catch (error) {
    showError(failureMessage(error));
  } finally {
    model.codeSending = false;
    renderCodeWait();
  }
});

$("#logout").addEventListener("click", async () => {
  if (model.pending) return;
  model.pending = true;
  $("#logout").disabled = true;
  showError("", "#account-error");
  try {
    await api("/api/logout", {});
    model.currentUser = null;
    model.pending = false;
    form.reset();
    $("#email").setCustomValidity("");
    setMode("login");
    $("#identifier").focus();
  } catch (error) {
    showError(failureMessage(error), "#account-error");
    if (!model.csrfToken) document.dispatchEvent(new Event("account-retry"));
  } finally {
    model.pending = false;
    $("#logout").disabled = false;
  }
});

$("#retry").addEventListener("click", initialize);

$("#verification-retry").addEventListener("click", initialize);

window.addEventListener("popstate", () => {
  if (location.pathname === "/verify") showVerification("", false);
  else if (model.currentUser) showAccount(model.currentUser);
  else
    setMode(location.pathname === "/register" ? "register" : "login", {
      navigate: false,
    });
});

window.addEventListener("hashchange", () => {
  if (location.pathname !== "/verify" || !location.hash) return;
  model.verificationToken = new URLSearchParams(location.hash.slice(1)).get(
    "token",
  );
  history.replaceState({}, "", "/verify");
  showVerification("", false);
});

$("#resend-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (model.pending || !model.csrfToken) return;
  model.pending = true;
  $("#verify-resend").disabled = true;
  showError("", "#verification-error");
  try {
    const response = await api("/api/verification/resend", {
      email: $("#verification-email").value.trim(),
    });
    $("#verification-status").textContent = response.message;
    $("#verification-status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#verification-error");
  } finally {
    model.pending = false;
    $("#verify-resend").disabled = !model.csrfToken;
  }
});

$("#verify-confirm").addEventListener("click", async () => {
  if (model.pending || !model.csrfToken || !model.verificationToken) return;
  model.pending = true;
  $("#verify-confirm").disabled = true;
  showError("", "#verification-error");
  try {
    const response = await api("/api/verification/confirm", {
      token: model.verificationToken,
    });
    model.verificationToken = null;
    model.pending = false;
    model.currentUser = null;
    setMode("login");
    $("#status").textContent = response.message;
    $("#status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#verification-error");
    $("#resend-form").hidden = false;
  } finally {
    model.pending = false;
    $("#verify-confirm").disabled = !model.csrfToken;
  }
});
