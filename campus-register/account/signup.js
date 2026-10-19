import { $, model } from "./context.js";

export function renderCodeWait() {
  const seconds = Math.max(
    0,
    Math.ceil((model.codeWaitUntil - Date.now()) / 1000),
  );
  $("#send-code").disabled =
    model.mode === "login" || model.codeSending || seconds > 0;
  $("#send-code-label").textContent = model.codeSending
    ? "正在发送…"
    : seconds
      ? `${seconds} 秒后重发`
      : "获取验证码";
}
