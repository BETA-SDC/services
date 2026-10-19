import { api } from "./client.js";

import { $ } from "./context.js";

export async function refreshPreferences() {
  const preferences = await api("/api/preferences");
  $("#global-theme").value = preferences.theme;
  applyGlobalTheme(preferences.theme);
}

export function applyGlobalTheme(theme) {
  const dark =
    theme === "dark" ||
    (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.body.dataset.theme = dark ? "dark" : "light";
}
