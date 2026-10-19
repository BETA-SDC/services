import { api } from "./client.js";

import { $, model } from "./context.js";

import { showError } from "./ui.js";

export async function refreshPublicProfile() {
  const state = await api("/api/site/session");
  if (!state.user) return;
  const profile = state.user.profile;
  $("#my-public-profile").href = profile.url;
  $("#my-public-profile").textContent = profile.url;
  if (!$("#public-bio-input").matches(":focus") && !model.publicBioDirty)
    $("#public-bio-input").value = profile.bio;
  const labels = {
    pending: "等待管理员审核",
    approved: "已批准",
    rejected: "已驳回",
    superseded: "已更新申请",
  };
  $("#profile-address-state").textContent = profile.request
    ? `${profile.request.slug} · ${labels[profile.request.state] || profile.request.state}`
    : "默认使用注册编号；自定义地址经管理员批准后生效。";
}

export function clearStudent() {
  model.studentVersion++;
  $("#profile-student").value = "";
  $("#profile-student").disabled = true;
  $("#student-save").disabled = true;
  $("#student-status").hidden = true;
  showError("", "#student-error");
}
