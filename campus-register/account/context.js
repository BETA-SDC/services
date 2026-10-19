export const model = {
  mode: location.pathname === "/register" ? "register" : "login",
  csrfToken: "",
  pending: false,
  currentUser: null,
  githubEnabled: false,
  githubAccount: null,
  codeWaitUntil: 0,
  codeSending: false,
  verificationToken:
    location.pathname === "/verify"
      ? new URLSearchParams(location.hash.slice(1)).get("token")
      : null,
  privatePolicy: "",
  privateBusy: false,
  privateTimer: undefined,
  privateViewVersion: 0,
  privateData: null,
  privateDocuments: {},
  privateVisibleUntil: 0,
  selectedSettings: "profile",
  factorPending: null,
  factorBusy: false,
  factorChallenge: "",
  factorEmailWait: 0,
  factorEmailTimer: undefined,
  publicBioDirty: false,
  studentVersion: 0,
};

export const $ = (selector) => document.querySelector(selector);

export const privatePageRequested = location.pathname === "/account/privacy";

export const form = $("#auth-form");

export const fields = $("#fields");

export const githubResult =
  location.pathname === "/account"
    ? new URLSearchParams(location.hash.slice(1)).get("github")
    : null;

if (githubResult) history.replaceState({}, "", "/account");

if (location.pathname === "/verify" && location.hash)
  history.replaceState({}, "", "/verify");

export const emailDomain = "@westlake.edu.cn";

export const defaultAvatarUrl = "/default-avatar.png";

$("#private-page").append($("#private-template").content.cloneNode(true));

export const privateLabels = { phone: "电话号码", document: "证件信息" };

export const allPrivateFields = Object.keys(privateLabels);

export const settingsPanels = {
  profile: "account-view",
  privacy: "private-page",
  security: "security-view",
  connections: "connections-view",
  preferences: "preferences-view",
};

export const settingsTitles = {
  profile: "个人资料",
  privacy: "隐私资料",
  security: "双重验证（2FA）",
  connections: "关联账户",
  preferences: "偏好设置",
};
