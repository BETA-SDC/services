import { $, fields, model } from "./context.js";
import { requestJSON } from "../shared/http.js";

export async function api(path, data, method) {
  try {
    const payload = await requestJSON(path, {
      data,
      method,
      csrf: model.csrfToken,
    });
    if (payload.csrf_token) model.csrfToken = payload.csrf_token;
    return payload;
  } catch (error) {
    if (error.payload?.csrf_token) model.csrfToken = error.payload.csrf_token;
    if (error.code === "csrf_failed") {
      model.csrfToken = "";
      fields.disabled = true;
      $("#retry").hidden = false;
      $("#verification-retry").hidden = false;
    }
    throw error;
  }
}
