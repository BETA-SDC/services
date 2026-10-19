import {labels, model, requestStates, states} from "./context.js";
import {requestJSON} from "../shared/http.js";

export const $ = selector => document.querySelector(selector);

export const fieldLabel = (event, field) => labels[field] || event.custom_fields?.find(item => item.id === field)?.label || field;

export const answerText = value => value && typeof value === "object"
  ? `${model.state.documents[value.type] || value.type} / ${value.country} / ${value.number}` : value;

export const icons = () => window.lucide?.createIcons();

export const stamp = value => new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
}).format(value * 1000);

export const localTime = value => new Date(value).toLocaleString("sv-SE", {timeZone: "Asia/Shanghai"}).slice(0, 16).replace(" ", "T");

export function notice(selector, value = "") {
  $(selector).textContent = value;
  $(selector).hidden = !value;
}

export async function api(path, data, method = "POST", headers = {}) {
  return requestJSON(path, {data, method: data === undefined ? "GET" : method,
    csrf: model.state?.csrf_token || "", headers});
}

export async function busy(button, work, errorSelector = "#error") {
  button.disabled = true;
  notice(errorSelector);
  try { await work(); }
  catch (error) { notice(errorSelector, error.name === "TimeoutError" ? "请求超时，请重试；重复提交不会重复签到。" : error.message); }
  finally {
    button.disabled = button.id === "previous" ? model.page <= 1 : button.id === "next" ? model.page * 50 >= model.total
      : button.id === "requests-previous" ? model.requestsPage <= 1
      : button.id === "requests-next" ? model.requestsPage * 50 >= model.requestsTotal
      : button.id === "access-previous" ? model.accessPage <= 1
      : button.id === "access-next" ? model.accessPage * 50 >= model.accessTotal
      : button.id === "toggle" ? model.selected?.status === "ended" : false;
  }
}

export function cell(row, value, heading = false) {
  const element = document.createElement(heading ? "th" : "td");
  element.textContent = value;
  row.append(element);
  return element;
}

export function badge(element, status) {
  element.className = "status";
  element.dataset.status = status;
  element.textContent = states[status] || status;
}

export function accessStatus(item) {
  if (item.status !== "approved") return requestStates[item.status];
  if (item.revoked_at) return "已撤销";
  if (item.expires_at <= Date.now() / 1000) return "已到期";
  return "已批准";
}
