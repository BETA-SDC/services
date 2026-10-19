import { model } from "./context.js";

import { initialize, setMode, showVerification } from "./views.js";

import "./auth-events.js";

import "./ui.js";

import "./client.js";

import "./github.js";

import "./signup.js";

import "./privacy.js";

import "./webauthn.js";

import "./privacy-events.js";

import "./settings.js";

import "./settings-events.js";

import "./factor.js";

import "./profile.js";

import "./preferences.js";

document.addEventListener("account-retry", initialize);

setMode(model.mode, { navigate: false });

if (location.pathname === "/verify") showVerification("", false);

initialize();
