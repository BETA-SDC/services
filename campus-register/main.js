const $ = (selector) => document.querySelector(selector);
const privatePageRequested = location.pathname === "/account/privacy";
const form = $("#auth-form");
const fields = $("#fields");
let mode = location.pathname === "/register" ? "register" : "login";
let csrfToken = "";
let pending = false;
let currentUser = null;
let githubEnabled = false;
let githubAccount = null;
let codeWaitUntil = 0;
let codeSending = false;
const githubResult = location.pathname === "/account"
  ? new URLSearchParams(location.hash.slice(1)).get("github") : null;
if (githubResult) history.replaceState({}, "", "/account");
let verificationToken = location.pathname === "/verify"
  ? new URLSearchParams(location.hash.slice(1)).get("token") : null;
if (location.pathname === "/verify" && location.hash) history.replaceState({}, "", "/verify");
const emailDomain = "@westlake.edu.cn";

$("#email").addEventListener("input", () => {
  $("#email-code").value = "";
  const input = $("#email");
  let local = input.value.trim();
  if (local.toLowerCase().endsWith(emailDomain) && local.indexOf("@") === local.lastIndexOf("@")) {
    local = local.slice(0, -emailDomain.length);
    input.value = local;
  }
  const valid = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$/.test(local)
    && !local.includes("..") && !local.startsWith(".") && !local.endsWith(".");
  input.setCustomValidity(!local || valid ? "" : "请填写有效的邮箱用户名，仅支持 @westlake.edu.cn 邮箱。");
});

function renderIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function showError(message, target = "#error") {
  const element = $(target);
  element.textContent = message;
  element.hidden = !message;
}

function resetPasswords() {
  for (const id of ["password", "confirm"]) {
    $(`#${id}`).value = "";
    $(`#${id}`).type = "password";
    const toggle = $(`[data-toggle-password="${id}"]`);
    toggle.setAttribute("aria-pressed", "false");
    toggle.setAttribute("aria-label", id === "confirm" ? "显示确认密码" : "显示密码");
    toggle.title = toggle.getAttribute("aria-label");
    toggle.innerHTML = '<i data-lucide="eye" aria-hidden="true"></i>';
  }
  $("#caps-lock").hidden = true;
  renderIcons();
}

function setMode(next, { navigate = true, preserveStatus = false } = {}) {
  if (pending) return;
  mode = next;
  const isLogin = mode === "login";
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
  $("#page-subtitle").textContent = isLogin ? "登录 BETA 账户中心。" : "一个账户，连接校园里的你我。";
  $("#submit-label").textContent = isLogin ? "登录" : "创建账户";
  $("#switch-label").textContent = isLogin ? "还没有账户？" : "已经有账户？";
  $("#switch-link").textContent = isLogin ? "创建账户" : "前往登录";
  $("#switch-link").href = isLogin ? "/register" : "/login";
  for (const id of ["real-name", "nickname", "email", "email-code", "confirm"]) {
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
  $("#github-option").hidden = isLogin || !githubEnabled;
  $("#send-code").disabled = isLogin || codeSending || Date.now() < codeWaitUntil;
  for (const name of ["register", "login"]) {
    $(`#${name}-tab`).removeAttribute("aria-current");
  }
  $(`#${mode}-tab`).setAttribute("aria-current", "page");
  for (const input of form.querySelectorAll("input")) input.removeAttribute("aria-invalid");
  resetPasswords();
  showError("");
  if (!preserveStatus) $("#status").hidden = true;
}

function showAccount(user, navigate = true) {
  currentUser = user;
  resetPasswords();
  $("#auth-view").hidden = true;
  $("#verification-view").hidden = true;
  $("#settings-view").hidden = false;
  $("#auth-identity").hidden = true;
  $(".auth-shell").classList.add("settings-shell");
  $("#account-name").textContent = user.nickname || user.real_name || user.username;
  $("#account-real-name").textContent = user.real_name || "待完善";
  $("#account-nickname").textContent = user.nickname || "未设置";
  $("#account-email").textContent = user.email;
  $("#profile-real-name").value = user.real_name || "";
  $("#profile-nickname").value = user.nickname || "";
  $("#profile-form").hidden = true;
  $("#profile-edit").hidden = false;
  $("#account-status").hidden = true;
  api("/api/session").then(state => {
    $("#admin-link").hidden = !state.access?.is_admin;
    $("#account-groups").textContent = state.access?.groups?.length
      ? `分组：${state.access.groups.map(group => group.name).join("、")}` : "尚未加入分组";
    const sync = state.access?.organization_sync;
    const states = {member: "BETA-SDC 成员已确认", not_member: "未加入 BETA-SDC",
      public_member: "已加入 BETA-SDC，组织角色与 Team 待授权",
      unavailable: "组织同步失败", permission_required: "组织权限尚未确认，请重新授权"};
    $("#organization-sync-state").textContent = sync
      ? Date.now() / 1000 - sync.synced_at >= 86400 ? "分组资料已过期，可重新同步"
        : states[sync.state] || "" : githubAccount ? "组织与 Team 尚未授权" : "";
    const owner = state.access?.github_owner;
    $("#owner-validity").hidden = !owner;
    $("#owner-validity").textContent = owner
      ? owner.expired ? "管理员身份已到期，请重新授权 GitHub"
        : `管理员身份有效至 ${new Date(owner.valid_until * 1000).toLocaleDateString("zh-CN")}` : "";
  }).catch(() => { $("#admin-link").hidden = true; });
  renderGithub();
  if (navigate) {
    const panel = location.pathname === "/account/privacy" ? "#privacy"
      : location.pathname === "/account" ? location.hash : "";
    history.replaceState({}, "", "/account" + panel);
  }
  document.dispatchEvent(new Event("account-ready"));
}

async function api(path, data, method) {
  const response = await fetch(path, {
    method: method || (data === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    headers: data === undefined ? {} : {
      "Content-Type": "application/json", "X-CSRF-Token": csrfToken,
    },
    body: data === undefined ? undefined : JSON.stringify(data),
    signal: AbortSignal.timeout(15000),
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(response.status === 429 ? "请求过于频繁，请稍后重试。" : "服务暂时不可用，请稍后重试。");
  }
  if (payload.csrf_token) csrfToken = payload.csrf_token;
  if (!response.ok) {
    if (response.status === 403) {
      csrfToken = "";
      fields.disabled = true;
      $("#retry").hidden = false;
      $("#verification-retry").hidden = false;
    }
    const error = new Error(payload.error || "请求失败，请稍后重试。");
    error.code = payload.code;
    error.email = payload.email;
    throw error;
  }
  return payload;
}

function failureMessage(error) {
  return ["TimeoutError", "AbortError", "TypeError"].includes(error.name)
    ? "连接失败，请检查校园网连接后重试。"
    : error.message;
}

async function initialize() {
  fields.disabled = true;
  $("#retry").hidden = true;
  $("#verification-retry").hidden = true;
  try {
    const state = await api("/api/session");
    githubEnabled = state.github_enabled;
    githubAccount = state.github;
    $("#github-option").hidden = mode === "login" || !githubEnabled;
    fields.disabled = false;
    showError("");
    if (location.pathname === "/verify") showVerification($("#verification-email").value, false);
    else if (state.user) {
      showAccount(state.user);
      if (githubResult) {
        const messages = {
          linked: "GitHub 账户已绑定。", cancelled: "已取消 GitHub 绑定。",
          invalid: "授权会话已失效，请重新发起绑定。",
          unavailable: "GitHub 暂时无法连接，请稍后重试。",
          conflict: "这个 GitHub 账户已绑定其他账户。",
          already: "请先解除原有绑定，再更换 GitHub 账户。",
        };
        $("#account-status").textContent = messages[githubResult] || "请重新发起 GitHub 绑定。";
        $("#account-status").hidden = false;
      }
    }
    else if (location.pathname === "/account" || privatePageRequested) setMode("login");
  } catch (error) {
    showError(failureMessage(error), location.pathname === "/verify" ? "#verification-error" : "#error");
    $("#retry").hidden = false;
    $("#verification-retry").hidden = false;
  }
}

for (const link of [$("#register-tab"), $("#login-tab"), $("#switch-link"), $("#verification-login")]) {
  link.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (pending) return;
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
$("#password").addEventListener("blur", () => { $("#caps-lock").hidden = true; });
$("#confirm").addEventListener("input", () => {
  $("#confirm").setCustomValidity("");
  $("#confirm").removeAttribute("aria-invalid");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pending || !csrfToken) return;
  if (mode === "register" && $("#password").value !== $("#confirm").value) {
    $("#confirm").setAttribute("aria-invalid", "true");
    showError("两次输入的密码不一致。");
    $("#confirm").focus();
    return;
  }
  pending = true;
  fields.disabled = true;
  showError("");
  const action = mode;
  $("#submit-label").textContent = action === "login" ? "正在登录…" : "正在创建…";
  const password = $("#password").value;
  try {
    if (action === "register") {
      const account = await api("/api/register", {
        real_name: $("#real-name").value.trim(), nickname: $("#nickname").value.trim(),
        email: $("#email").value.trim() + emailDomain,
        password, email_code: $("#email-code").value.trim(),
      });
      const bindGithub = githubEnabled && $("#github-after-register").checked;
      pending = false;
      $("#identifier").value = account.email;
      setMode("login");
      $("#status").textContent = "邮箱已验证，账户创建成功。请登录后添加通行密钥，建议使用指纹或面容验证。";
      $("#status").hidden = false;
      sessionStorage.setItem("bindGithubAfterLogin", bindGithub ? "1" : "0");
      sessionStorage.setItem("suggestPasskeySetup", "1");
    } else {
      const state = await api("/api/login", { identifier: $("#identifier").value.trim(), password });
      const accountState = await api("/api/session");
      githubAccount = accountState.github;
      githubEnabled = accountState.github_enabled;
      showAccount(state.user);
      $("#logout").focus();
      if (sessionStorage.getItem("bindGithubAfterLogin") === "1") {
        sessionStorage.removeItem("bindGithubAfterLogin");
        pending = false;
        await bindGithub();
      }
    }
  } catch (error) {
    if (error.code === "email_verification_required") {
      verificationToken = null;
      pending = false;
      showVerification(error.email);
    } else showError(failureMessage(error));
  } finally {
    pending = false;
    fields.disabled = !csrfToken;
    $("#submit-label").textContent = mode === "login" ? "登录" : "创建账户";
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
  $("#profile-real-name").value = currentUser?.real_name || "";
  $("#profile-nickname").value = currentUser?.nickname || "";
});
$("#profile-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (pending || !csrfToken) return;
  pending = true;
  const save = $("#profile-form button[type=submit]");
  save.disabled = true;
  showError("", "#account-error");
  try {
    const state = await api("/api/profile", {
      real_name: $("#profile-real-name").value.trim(), nickname: $("#profile-nickname").value.trim(),
    });
    showAccount(state.user, false);
    $("#account-status").textContent = "资料已保存。";
    $("#account-status").hidden = false;
  } catch (error) { showError(failureMessage(error), "#account-error"); }
  finally { pending = false; save.disabled = false; }
});

const defaultAvatarUrl = "/default-avatar.png";

function renderGithub() {
  const accountAvatar = $("#account-avatar");
  accountAvatar.classList.toggle("default-avatar", !githubAccount?.avatar_url);
  accountAvatar.alt = githubAccount ? `${githubAccount.login} 的头像` : "默认头像";
  accountAvatar.src = githubAccount?.avatar_url || defaultAvatarUrl;
  $("#github-section").hidden = !githubEnabled && !githubAccount;
  $("#github-status").textContent = githubAccount ? `@${githubAccount.login}` : "未绑定";
  $("#github-link").hidden = Boolean(githubAccount);
  $("#github-unlink").hidden = !githubAccount;
  $("#github-profile").hidden = !githubAccount;
  $("#github-refresh").hidden = !githubAccount || !githubEnabled;
  $("#github-authorize").hidden = !githubAccount || !githubEnabled;
  const avatar = $("#github-avatar");
  avatar.hidden = true;
  avatar.removeAttribute("src");
  $("#github-avatar-fallback").hidden = false;
  if (githubAccount) {
    $("#github-name").textContent = githubAccount.name || githubAccount.login;
    $("#github-homepage").textContent = `@${githubAccount.login}`;
    $("#github-homepage").href = githubAccount.html_url;
    const organizations = $("#github-organizations");
    organizations.replaceChildren();
    for (const organization of githubAccount.organizations) {
      const link = document.createElement("a");
      link.textContent = organization.login;
      link.href = organization.html_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      organizations.append(link);
    }
    $("#github-org-empty").hidden = githubAccount.organizations.length > 0;
    $("#github-org-empty").textContent = githubAccount.organizations_complete
      ? "未公开组织成员身份" : "组织资料暂未完整同步";
    $("#github-org-incomplete").hidden = githubAccount.organizations_complete || !githubAccount.organizations.length;
    $("#github-synced").textContent = githubAccount.synced_at
      ? `更新于 ${new Date(githubAccount.synced_at * 1000).toLocaleDateString("zh-CN")}` : "尚未同步资料";
    avatar.alt = `${githubAccount.login} 的 GitHub 头像`;
    avatar.src = githubAccount.avatar_url;
  }
}
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

async function bindGithub() {
  if (pending || !csrfToken) return;
  pending = true;
  $("#github-link").disabled = true;
  showError("", "#account-error");
  try {
    const response = await api("/api/github/link", {});
    location.assign(response.url);
  } catch (error) {
    showError(failureMessage(error), "#account-error");
    if (!csrfToken) await initialize();
  } finally {
    pending = false;
    $("#github-link").disabled = false;
  }
}
$("#github-link").addEventListener("click", bindGithub);
$("#github-authorize").addEventListener("click", bindGithub);
$("#github-refresh").addEventListener("click", async () => {
  if (pending || !csrfToken) return;
  pending = true;
  $("#github-refresh").disabled = true;
  showError("", "#account-error");
  try {
    const response = await api("/api/github/refresh", {});
    githubAccount = response.github;
    renderGithub();
    $("#account-status").textContent = "GitHub 资料已更新。";
    $("#account-status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#account-error");
  } finally {
    pending = false;
    $("#github-refresh").disabled = false;
  }
});
$("#github-unlink").addEventListener("click", async () => {
  if (pending || !csrfToken) return;
  pending = true;
  $("#github-unlink").disabled = true;
  showError("", "#account-error");
  try {
    const response = await api("/api/github/unlink", {});
    githubAccount = response.github;
    renderGithub();
    $("#account-status").textContent = "已解除 GitHub 绑定。";
    $("#account-status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#account-error");
    if (!csrfToken) await initialize();
  } finally {
    pending = false;
    $("#github-unlink").disabled = false;
  }
});

function renderCodeWait() {
  const seconds = Math.max(0, Math.ceil((codeWaitUntil - Date.now()) / 1000));
  $("#send-code").disabled = mode === "login" || codeSending || seconds > 0;
  $("#send-code-label").textContent = codeSending ? "正在发送…" : seconds ? `${seconds} 秒后重发` : "获取验证码";
}
setInterval(renderCodeWait, 1000);
$("#send-code").addEventListener("click", async () => {
  if (pending || codeSending || !csrfToken || Date.now() < codeWaitUntil) return;
  if (!$("#email").reportValidity()) return;
  codeSending = true;
  renderCodeWait();
  showError("");
  try {
    const response = await api("/api/signup/code", { email: $("#email").value.trim() + emailDomain });
    codeWaitUntil = Date.now() + response.retry_after * 1000;
    $("#code-status").textContent = response.message;
    $("#code-status").hidden = false;
    $("#email-code").focus();
  } catch (error) {
    showError(failureMessage(error));
  } finally {
    codeSending = false;
    renderCodeWait();
  }
});

$("#logout").addEventListener("click", async () => {
  if (pending) return;
  pending = true;
  $("#logout").disabled = true;
  showError("", "#account-error");
  try {
    await api("/api/logout", {});
    currentUser = null;
    pending = false;
    form.reset();
    $("#email").setCustomValidity("");
    setMode("login");
    $("#identifier").focus();
  } catch (error) {
    showError(failureMessage(error), "#account-error");
    if (!csrfToken) await initialize();
  } finally {
    pending = false;
    $("#logout").disabled = false;
  }
});
$("#retry").addEventListener("click", initialize);
$("#verification-retry").addEventListener("click", initialize);
window.addEventListener("popstate", () => {
  if (location.pathname === "/verify") showVerification("", false);
  else if (currentUser) showAccount(currentUser);
  else setMode(location.pathname === "/register" ? "register" : "login", { navigate: false });
});
function showVerification(email = "", navigate = true) {
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
  $("#verification-copy").textContent = verificationToken
    ? "点击下方按钮，确认验证此邮箱。"
    : "请检查收件箱和垃圾邮件。验证链接 30 分钟内有效。";
  $("#verify-confirm").hidden = !verificationToken;
  $("#verify-confirm").disabled = !csrfToken;
  $("#verify-resend").disabled = !csrfToken;
  $("#resend-form").hidden = Boolean(verificationToken);
  document.title = "邮箱验证 · BETA";
  if (navigate) history.pushState({}, "", "/verify");
}

window.addEventListener("hashchange", () => {
  if (location.pathname !== "/verify" || !location.hash) return;
  verificationToken = new URLSearchParams(location.hash.slice(1)).get("token");
  history.replaceState({}, "", "/verify");
  showVerification("", false);
});

$("#resend-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pending || !csrfToken) return;
  pending = true;
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
    pending = false;
    $("#verify-resend").disabled = !csrfToken;
  }
});

$("#verify-confirm").addEventListener("click", async () => {
  if (pending || !csrfToken || !verificationToken) return;
  pending = true;
  $("#verify-confirm").disabled = true;
  showError("", "#verification-error");
  try {
    const response = await api("/api/verification/confirm", { token: verificationToken });
    verificationToken = null;
    pending = false;
    currentUser = null;
    setMode("login");
    $("#status").textContent = response.message;
    $("#status").hidden = false;
  } catch (error) {
    showError(failureMessage(error), "#verification-error");
    $("#resend-form").hidden = false;
  } finally {
    pending = false;
    $("#verify-confirm").disabled = !csrfToken;
  }
});

setMode(mode, { navigate: false });
if (location.pathname === "/verify") showVerification("", false);
initialize();
