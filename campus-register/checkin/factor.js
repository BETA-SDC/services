import {model} from "./context.js";
import {$, api, busy, notice} from "./ui.js";

export async function ensureFactor() {
  if (model.factorPromise) return model.factorPromise;
  model.factorPromise = verifyFactor();
  try { await model.factorPromise; }
  finally { model.factorPromise = null; }
}

export async function verifyFactor() {
  const security = await api("/api/security");
  if (security.verification) return;
  $("#factor-passkey").hidden = !security.passkey_count || !window.PublicKeyCredential;
  $("#factor-message").textContent = `绑定邮箱：${security.email}`;
  $("#factor-code").value = "";
  model.emailChallenge = null;
  notice("#factor-error");
  $("#factor-dialog").showModal();
  await new Promise((resolve, reject) => { model.factorResolve = resolve; model.factorReject = reject; });
}

export function verified() {
  const resolve = model.factorResolve;
  model.factorResolve = model.factorReject = null;
  $("#factor-dialog").close();
  resolve?.();
}

$("#factor-dialog").addEventListener("close", () => {
  const reject = model.factorReject;
  model.factorResolve = model.factorReject = null;
  model.emailChallenge = null;
  $("#factor-code").value = "";
  reject?.(new Error("二次验证已取消。"));
});

$("#factor-send").addEventListener("click", () => busy($("#factor-send"), async () => {
  const result = await api("/api/security/email/send", {});
  model.emailChallenge = result.challenge;
  $("#factor-message").textContent = "验证码已申请发送，5 分钟内有效。";
  $("#factor-code").focus();
}, "#factor-error"));

$("#factor-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    if (!model.emailChallenge) throw new Error("请先发送验证码。");
    await api("/api/security/email/verify", {challenge: model.emailChallenge, code: $("#factor-code").value});
    verified();
  }, "#factor-error");
});

export const decode = value => Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));

export const encode = value => btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

$("#factor-passkey").addEventListener("click", () => busy($("#factor-passkey"), async () => {
  const result = await api("/api/passkeys/stepup/options", {consent: true});
  const options = result.options.publicKey;
  options.challenge = decode(options.challenge);
  options.allowCredentials = options.allowCredentials.map(item => ({...item, id: decode(item.id)}));
  const credential = await navigator.credentials.get({publicKey: options});
  if (!credential) throw new Error("通行密钥验证已取消。");
  await api("/api/passkeys/stepup/complete", {flow: result.flow, credential: {
    id: credential.id, rawId: encode(credential.rawId), type: credential.type,
    response: {clientDataJSON: encode(credential.response.clientDataJSON),
      authenticatorData: encode(credential.response.authenticatorData),
      signature: encode(credential.response.signature),
      userHandle: credential.response.userHandle ? encode(credential.response.userHandle) : null},
    clientExtensionResults: credential.getClientExtensionResults(),
  }});
  verified();
}, "#factor-error"));
