import { $, model, settingsPanels, settingsTitles } from "./context.js";

export function selectSettings(name, navigate = true) {
  model.selectedSettings = name in settingsPanels ? name : "profile";
  $("#public-settings").hidden = model.selectedSettings !== "profile";
  for (const [key, id] of Object.entries(settingsPanels))
    $(`#${id}`).hidden = key !== model.selectedSettings;
  for (const link of document.querySelectorAll("[data-settings]")) {
    if (link.dataset.settings === model.selectedSettings)
      link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (navigate) history.pushState({}, "", "/account#" + model.selectedSettings);
  document.title = `${settingsTitles[model.selectedSettings]} · BETA`;
  document.dispatchEvent(
    new CustomEvent("settings-panel", { detail: model.selectedSettings }),
  );
}
