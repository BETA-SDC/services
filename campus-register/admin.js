const $ = (selector) => document.querySelector(selector);
let csrf = "";
let data = {users: [], groups: [], audit: [], page: 1, total: 0};
let page = 1;
let busy = false;
let editing = null;
let myName = "";
const labels = {member: "已确认成员", public_member: "已确认公开成员，角色待授权",
  not_member: "不是成员", unavailable: "同步失败", permission_required: "组织权限待确认"};
const actions = {"group.create": "创建分组", "group.rename": "重命名分组", "group.archive": "归档分组", "membership.add": "加入分组", "membership.remove": "移出分组", "role.admin": "授予管理员", "role.member": "移除管理员", "role.bootstrap.host": "初始化管理员"};
actions["role.github_owner.grant"] = "GitHub Owner 自动授权";
actions["role.github_owner.revoke"] = "GitHub Owner 自动撤权";
actions["github.owner.cli_verified"] = "GitHub Owner CLI 核验同步";
actions["profile.name.host"] = "按申请修正姓名";

function node(tag, text) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  return element;
}
function message(text, error = false) {
  const target = $(error ? "#admin-error" : "#admin-status");
  target.textContent = text;
  target.hidden = !text;
}
async function api(path, payload) {
  const response = await fetch(path, {
    method: payload === undefined ? "GET" : "POST", credentials: "same-origin",
    headers: payload === undefined ? {} : {"Content-Type": "application/json", "X-CSRF-Token": csrf},
    body: payload === undefined ? undefined : JSON.stringify(payload),
    signal: AbortSignal.timeout(15000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "请求失败");
  return result;
}
async function change(path, payload) {
  if (busy) return;
  busy = true;
  message("", true);
  try {
    await api(path, payload);
    await load();
    message("已保存");
    return true;
  } catch (error) {
    message(error.message, true);
    return false;
  } finally { busy = false; }
}
function option(select, label, value) {
  const item = node("option", label);
  item.value = value;
  select.append(item);
}
function button(label, handler) {
  const element = node("button", label);
  element.className = "secondary";
  element.type = "button";
  element.addEventListener("click", handler);
  return element;
}
function renderMembers() {
  const filter = $("#group-filter").value;
  const body = $("#members-body");
  body.replaceChildren();
  for (const user of data.users.filter(user => !filter || user.group_ids.includes(Number(filter)))) {
    const row = node("tr");
    const account = node("td", user.real_name || "姓名待完善");
    if (user.nickname) account.append(node("small", `昵称：${user.nickname}`));
    account.append(node("small", user.email), node("small", user.email_verified_at ? "邮箱已验证" : "邮箱未验证"));
    row.append(account, node("td", user.github_login ? `@${user.github_login}` : "未绑定"));
    const status = node("td", labels[user.organization_state] || "尚未授权同步");
    if (user.organization_synced_at) {
      status.append(node("small", new Date(user.organization_synced_at * 1000).toLocaleString("zh-CN")));
      status.append(node("small", Date.now() / 1000 - user.organization_synced_at >= 86400
        ? "成员资格已过期，需重新授权" : user.structure_complete ? "组织完整结构" : "授权账号可见结构"));
    }
    const roleCell = node("td", user.role_source === "github_owner" ? "管理员 · GitHub Owner"
      : user.role === "admin" ? "管理员" : "普通成员");
    if (user.admin_valid_until) roleCell.append(node("small",
      `有效至 ${new Date(user.admin_valid_until * 1000).toLocaleDateString("zh-CN")}`));
    row.append(status, roleCell);
    row.append(node("td", data.groups.filter(group => user.group_ids.includes(group.id)).map(group => group.name).join("、") || "未分组"));
    const controls = node("td");
    controls.append(button("分组", () => editMembership(user)));
    const role = button(user.role === "admin" ? "移除管理员" : "设为管理员", () => {
      if (confirm(`确认修改 ${user.real_name || user.nickname || user.email} 的管理员权限？`)) change("/api/admin/role", {
        user_id: user.id, role: user.role === "admin" ? "member" : "admin"});
    });
    role.disabled = user.username === myName || !user.email_verified_at || user.role_source === "github_owner";
    controls.append(role);
    row.append(controls);
    body.append(row);
  }
  if (!body.children.length) {
    const cell = node("td", filter ? "本页暂无该分组成员" : "暂无账户");
    cell.colSpan = 6;
    const row = node("tr");
    row.append(cell);
    body.append(row);
  }
  $("#member-count").textContent = `共 ${data.total} 个账户`;
  $("#page-label").textContent = `${data.page} / ${Math.max(1, Math.ceil(data.total / 50))}`;
  $("#previous").disabled = page <= 1;
  $("#next").disabled = page * 50 >= data.total;
}
function renderGroups() {
  const tree = $("#group-tree");
  tree.replaceChildren();
  const seen = new Set();
  function append(group, parent, depth) {
    if (seen.has(group.id)) return;
    seen.add(group.id);
    const container = node("div");
    const row = node("div");
    row.className = "group-node";
    row.append(node("strong", group.name), node("small", `${group.source === "github" ? "GitHub · 只读" : "本地"} · ${group.member_count} 人`));
    if (group.source === "local") {
      const controls = node("span");
      controls.className = "group-actions";
      controls.append(button("重命名", () => {
        const name = prompt("分组名称", group.name);
        if (name?.trim()) change("/api/admin/group/update", {group_id: group.id, name: name.trim()});
      }), button("归档", () => {
        if (confirm(`归档分组 ${group.name}？`)) change("/api/admin/group/update", {group_id: group.id, archived: true});
      }));
      row.append(controls);
    }
    container.append(row);
    const children = node("div");
    if (depth < 5) children.className = "tree-children";
    for (const child of data.groups.filter(item => item.parent_id === group.id)) append(child, children, depth + 1);
    container.append(children);
    parent.append(container);
  }
  for (const group of data.groups.filter(item => item.parent_id === null)) append(group, tree, 0);
  for (const group of data.groups) append(group, tree, 0);
  $("#groups-empty").hidden = Boolean(data.groups.length);
}
function editMembership(user) {
  editing = user;
  $("#membership-title").textContent = `${user.real_name || user.nickname || user.email} 的分组`;
  $("#membership-options").replaceChildren();
  for (const group of data.groups) {
    const label = node("label");
    const input = node("input");
    input.type = "checkbox";
    input.value = group.id;
    input.checked = user.group_ids.includes(group.id);
    input.disabled = group.source === "github";
    label.append(input, node("span", `${group.name}${group.source === "github" ? " · GitHub" : ""}`));
    $("#membership-options").append(label);
  }
  $("#membership-dialog").showModal();
}
async function load() {
  const selection = $("#group-filter").value;
  data = await api(`/api/admin/overview?page=${page}${selection ? `&group=${encodeURIComponent(selection)}` : ""}`);
  $("#group-filter").replaceChildren();
  $("#group-parent").replaceChildren();
  option($("#group-filter"), "全部", "");
  option($("#group-parent"), "无", "");
  for (const group of data.groups) {
    option($("#group-filter"), group.name, group.id);
    if (group.source === "local") option($("#group-parent"), group.name, group.id);
  }
  $("#group-filter").value = selection;
  renderMembers();
  renderGroups();
  const addresses = await api("/api/admin/profile-addresses");
  $("#addresses-body").replaceChildren();
  for (const entry of addresses.requests) {
    const row = node("tr");
    row.append(node("td", entry.nickname || entry.username), node("td", entry.slug),
      node("td", new Date(entry.created_at * 1000).toLocaleString("zh-CN")));
    const controls = node("td");
    for (const [label, decision] of [["批准", "approve"], ["驳回", "reject"]]) {
      controls.append(button(label, () => {
        if (confirm(`${label}主页地址 ${entry.slug}？`)) change("/api/admin/profile-addresses", {id: entry.id, decision});
      }));
    }
    row.append(controls);
    $("#addresses-body").append(row);
  }
  if (!addresses.requests.length) {
    const row = node("tr"), cell = node("td", "暂无待审核的主页地址");
    cell.colSpan = 4; row.append(cell); $("#addresses-body").append(row);
  }
  $("#audit-body").replaceChildren();
  for (const entry of data.audit) {
    const row = node("tr");
    for (const text of [new Date(entry.created_at * 1000).toLocaleString("zh-CN"), entry.actor || "系统", actions[entry.action] || entry.action, entry.target]) row.append(node("td", text));
    $("#audit-body").append(row);
  }
  if (!data.audit.length) {
    const row = node("tr");
    const cell = node("td", "暂无操作记录");
    cell.colSpan = 4;
    row.append(cell);
    $("#audit-body").append(row);
  }
}
$("#create-group").addEventListener("submit", async event => {
  event.preventDefault();
  if (await change("/api/admin/groups", {name: $("#group-name").value.trim(), parent_id: Number($("#group-parent").value) || null})) $("#group-name").value = "";
});
$("#edit-membership").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || !editing) return;
  busy = true;
  try {
    const changes = [...$("#membership-options").querySelectorAll("input:not(:disabled)")].map(input => ({
      group_id: Number(input.value), member: input.checked,
    })).filter(item => item.member !== editing.group_ids.includes(item.group_id));
    await api("/api/admin/memberships", {user_id: editing.id, changes});
    $("#membership-dialog").close();
    await load();
    message("已保存");
  } catch (error) { message(error.message, true); } finally { busy = false; }
});
$("#cancel-membership").addEventListener("click", () => $("#membership-dialog").close());
$("#group-filter").addEventListener("change", () => { page = 1; load().catch(error => message(error.message, true)); });
$("#reload").addEventListener("click", () => load().catch(error => message(error.message, true)));
$("#previous").addEventListener("click", () => { page--; load().catch(error => message(error.message, true)); });
$("#next").addEventListener("click", () => { page++; load().catch(error => message(error.message, true)); });
for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => {
  for (const tab of document.querySelectorAll("[data-view]")) tab.setAttribute("aria-selected", String(tab === button));
  for (const name of ["members", "groups", "addresses", "audit"]) $(`#${name}-view`).hidden = name !== button.dataset.view;
});
(async () => {
  try {
    const state = await api("/api/session");
    csrf = state.csrf_token;
    myName = state.user?.username || "";
    if (!state.access?.is_admin) throw new Error("需要管理员权限。");
    await load();
  } catch (error) { message(error.message, true); }
  window.lucide?.createIcons();
})();
