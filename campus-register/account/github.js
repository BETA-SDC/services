import { api } from "./client.js";

import { $, defaultAvatarUrl, model } from "./context.js";

import { failureMessage, showError } from "./ui.js";

export function renderGithub() {
  const accountAvatar = $("#account-avatar");
  accountAvatar.classList.toggle(
    "default-avatar",
    !model.githubAccount?.avatar_url,
  );
  accountAvatar.alt = model.githubAccount
    ? `${model.githubAccount.login} 的头像`
    : "默认头像";
  accountAvatar.src = model.githubAccount?.avatar_url || defaultAvatarUrl;
  $("#github-section").hidden = !model.githubEnabled && !model.githubAccount;
  $("#github-status").textContent = model.githubAccount
    ? `@${model.githubAccount.login}`
    : "未绑定";
  $("#github-link").hidden = Boolean(model.githubAccount);
  $("#github-unlink").hidden = !model.githubAccount;
  $("#github-profile").hidden = !model.githubAccount;
  $("#github-refresh").hidden = !model.githubAccount || !model.githubEnabled;
  $("#github-authorize").hidden = !model.githubAccount || !model.githubEnabled;
  const avatar = $("#github-avatar");
  avatar.hidden = true;
  avatar.removeAttribute("src");
  $("#github-avatar-fallback").hidden = false;
  if (model.githubAccount) {
    $("#github-name").textContent =
      model.githubAccount.name || model.githubAccount.login;
    $("#github-homepage").textContent = `@${model.githubAccount.login}`;
    $("#github-homepage").href = model.githubAccount.html_url;
    const organizations = $("#github-organizations");
    organizations.replaceChildren();
    for (const organization of model.githubAccount.organizations) {
      const link = document.createElement("a");
      link.textContent = organization.login;
      link.href = organization.html_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      organizations.append(link);
    }
    $("#github-org-empty").hidden =
      model.githubAccount.organizations.length > 0;
    $("#github-org-empty").textContent = model.githubAccount
      .organizations_complete
      ? "未公开组织成员身份"
      : "组织资料暂未完整同步";
    $("#github-org-incomplete").hidden =
      model.githubAccount.organizations_complete ||
      !model.githubAccount.organizations.length;
    $("#github-synced").textContent = model.githubAccount.synced_at
      ? `更新于 ${new Date(model.githubAccount.synced_at * 1000).toLocaleDateString("zh-CN")}`
      : "尚未同步资料";
    avatar.alt = `${model.githubAccount.login} 的 GitHub 头像`;
    avatar.src = model.githubAccount.avatar_url;
  }
}

export async function bindGithub() {
  if (model.pending || !model.csrfToken) return;
  model.pending = true;
  $("#github-link").disabled = true;
  showError("", "#account-error");
  try {
    const response = await api("/api/github/link", {});
    location.assign(response.url);
  } catch (error) {
    showError(failureMessage(error), "#account-error");
    if (!model.csrfToken) document.dispatchEvent(new Event("account-retry"));
  } finally {
    model.pending = false;
    $("#github-link").disabled = false;
  }
}
