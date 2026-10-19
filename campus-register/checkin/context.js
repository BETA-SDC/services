export const model = {
  state: undefined,
  selected: undefined,
  joinEvent: undefined,
  page: 1,
  total: 0,
  qrTimer: undefined,
  factorResolve: undefined,
  factorReject: undefined,
  emailChallenge: undefined,
  calendarEvents: [],
  calendarRequest: 0,
  reviewRequest: undefined,
  factorPromise: undefined,
  pageData: undefined,
  recordPAT: "",
  recordEvent: undefined,
  accessReview: undefined,
  recordExpiryTimer: undefined,
  accessPage: 1,
  accessTotal: 0,
  requestsPage: 1,
  requestsTotal: 0
};

export const joinToken = location.pathname.match(/^\/checkin\/join\/([A-Za-z0-9_-]{43})$/)?.[1];

export const detailPath = location.pathname.match(/^\/checkin\/events\/\d+\/[^/]+$/)?.[0];

export const requestsView = location.pathname === "/checkin/requests";

export const accessView = location.pathname === "/checkin/access-requests";

export const recordsId = location.pathname.match(/^\/checkin\/records\/([a-f0-9]{32})$/)?.[1];

export const labels = {name: "姓名", email: "邮箱", student_id: "学号", phone: "手机号码", document: "身份证／其他证件"};

export const states = {open: "签到中", scheduled: "未开始", closed: "已暂停", ended: "已结束"};

export const prefilled = new Set();

export const requestStates = {pending: "待审核", approved: "已批准", rejected: "已驳回"};
