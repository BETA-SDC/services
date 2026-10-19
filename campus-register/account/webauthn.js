import { api } from "./client.js";

export function decodeCredential(value) {
  const text = value.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(
    atob(text + "=".repeat((4 - (text.length % 4)) % 4)),
    (c) => c.charCodeAt(0),
  );
}

export function encodeCredential(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export async function webauthn(kind, payload) {
  const start = await api(`/api/passkeys/${kind}/options`, payload);
  const publicKey = start.options.publicKey;
  publicKey.challenge = decodeCredential(publicKey.challenge);
  for (const name of ["allowCredentials", "excludeCredentials"]) {
    if (publicKey[name])
      publicKey[name] = publicKey[name].map((item) => ({
        ...item,
        id: decodeCredential(item.id),
      }));
  }
  if (publicKey.user) publicKey.user.id = decodeCredential(publicKey.user.id);
  const credential =
    kind === "register"
      ? await navigator.credentials.create({ publicKey })
      : await navigator.credentials.get({ publicKey });
  if (!credential) throw new Error("通行密钥验证已取消。");
  const result = {
    id: credential.id,
    rawId: encodeCredential(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: encodeCredential(credential.response.clientDataJSON),
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
  if (kind === "register") {
    result.response.attestationObject = encodeCredential(
      credential.response.attestationObject,
    );
    result.response.transports = credential.response.getTransports?.() || [];
  } else {
    result.response.authenticatorData = encodeCredential(
      credential.response.authenticatorData,
    );
    result.response.signature = encodeCredential(credential.response.signature);
    result.response.userHandle = credential.response.userHandle
      ? encodeCredential(credential.response.userHandle)
      : null;
  }
  return api(`/api/passkeys/${kind}/complete`, {
    flow: start.flow,
    credential: result,
  });
}
