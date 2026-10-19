import { $ } from "./context.js";

export function renderIcons() {
  if (window.lucide) window.lucide.createIcons();
}

export function showError(message, target = "#error") {
  const element = $(target);
  element.textContent = message;
  element.hidden = !message;
}

export function resetPasswords() {
  for (const id of ["password", "confirm"]) {
    $(`#${id}`).value = "";
    $(`#${id}`).type = "password";
    const toggle = $(`[data-toggle-password="${id}"]`);
    toggle.setAttribute("aria-pressed", "false");
    toggle.setAttribute(
      "aria-label",
      id === "confirm" ? "显示确认密码" : "显示密码",
    );
    toggle.title = toggle.getAttribute("aria-label");
    toggle.innerHTML = '<i data-lucide="eye" aria-hidden="true"></i>';
  }
  $("#caps-lock").hidden = true;
  renderIcons();
}

export function failureMessage(error) {
  return ["TimeoutError", "AbortError", "TypeError"].includes(error.name)
    ? "连接失败，请检查校园网连接后重试。"
    : error.message;
}
