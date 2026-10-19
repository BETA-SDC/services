import {model, prefilled} from "./context.js";
import {$} from "./ui.js";

export function clearSensitive() {
  clearTimeout(model.recordExpiryTimer);
  model.recordPAT = "";
  model.recordEvent = null;
  $("#record-pat").value = "";
  $("#record-section").hidden = true;
  for (const field of prefilled) {
    if (field === "document") {
      for (const input of $("#answer-fields").querySelectorAll('[name^="document_"]')) input.value = "";
      continue;
    }
    const input = $(`#answer-fields input[name="${field}"]`);
    if (input) input.value = "";
  }
  prefilled.clear();
  $("#records-body").replaceChildren();
}
