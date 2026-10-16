"use strict";

const classNames = {
  recyclable: "可回收物",
  kitchen: "厨余垃圾",
  hazardous: "有害垃圾",
  other: "其他垃圾",
};

const detailNames = {
  event_id: "记录编号",
  task_id: "任务编号",
  timestamp_ms: "记录时间",
  class_id: "类别编号",
  confidence: "识别可信度",
  u_px: "图像横坐标（像素）",
  v_px: "图像纵坐标（像素）",
  target_x_m: "机械臂 X（米）",
  target_y_m: "机械臂 Y（米）",
  target_z_m: "机械臂 Z（米）",
  result: "分拣结果",
  error_code: "问题编号",
  duration_ms: "耗时（毫秒）",
  calibration_id: "校准版本",
};

const state = { page: 1, pageSize: 50, totalPages: 1 };
const elements = {
  date: document.querySelector("#filter-date"),
  classId: document.querySelector("#filter-class"),
  result: document.querySelector("#filter-result"),
  pageSize: document.querySelector("#page-size"),
  body: document.querySelector("#records-body"),
  count: document.querySelector("#records-count"),
  pageSummary: document.querySelector("#page-summary"),
  pageIndicator: document.querySelector("#page-indicator"),
  previous: document.querySelector("#previous-page"),
  next: document.querySelector("#next-page"),
  error: document.querySelector("#records-error"),
  dialog: document.querySelector("#event-dialog"),
  details: document.querySelector("#event-details"),
};

function formatLocalTime(timestampMs) {
  if (timestampMs === null || timestampMs === undefined || timestampMs === "") return "—";
  const value = new Date(Number(timestampMs));
  if (Number.isNaN(value.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(value);
}

function localDateRange(dateValue) {
  if (!dateValue) return null;
  const [year, month, day] = dateValue.split("-").map(Number);
  const start = new Date(year, month - 1, day);
  const end = new Date(year, month - 1, day + 1);
  return { start_time_ms: start.getTime(), end_time_ms: end.getTime() };
}

function currentFilters() {
  const params = new URLSearchParams();
  const range = localDateRange(elements.date.value);
  if (range) {
    params.set("start_time_ms", String(range.start_time_ms));
    params.set("end_time_ms", String(range.end_time_ms));
  }
  if (elements.classId.value) params.set("class_id", elements.classId.value);
  if (elements.result.value) params.set("result", elements.result.value);
  return params;
}

function makeCell(text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
}

function showEmptyMessage(message) {
  const row = document.createElement("tr");
  const cell = makeCell(message, "table-message");
  cell.colSpan = 8;
  row.append(cell);
  elements.body.replaceChildren(row);
}

function renderRows(items) {
  if (!items.length) {
    showEmptyMessage("当前筛选条件下没有记录");
    return;
  }

  const rows = items.map((item) => {
    const row = document.createElement("tr");
    row.append(makeCell(formatLocalTime(item.timestamp_ms)));
    row.append(makeCell(classNames[item.class_id] || item.class_id, "class-label"));
    row.append(makeCell(`${(Number(item.confidence) * 100).toFixed(1)}%`, "confidence-cell"));
    row.append(makeCell(`${item.u_px}, ${item.v_px}`, "mono"));
    const target = [item.target_x_m, item.target_y_m, item.target_z_m]
      .some((value) => value === null || value === undefined)
      ? "—"
      : `${item.target_x_m}, ${item.target_y_m}, ${item.target_z_m}`;
    row.append(makeCell(target, "mono"));

    const resultCell = document.createElement("td");
    const resultTag = document.createElement("span");
    resultTag.className = `result-tag ${item.result === "success" ? "result-success" : item.result === "failed" ? "result-failed" : "result-other"}`;
    resultTag.textContent = item.result === "success" ? "成功" : item.result === "failed" ? "失败" : item.result;
    resultCell.append(resultTag);
    row.append(resultCell);
    row.append(makeCell(`${item.duration_ms} ms`));

    const actionCell = document.createElement("td");
    const detailButton = document.createElement("button");
    detailButton.className = "detail-trigger";
    detailButton.type = "button";
    detailButton.textContent = "详情";
    detailButton.addEventListener("click", () => loadDetail(item.event_id));
    actionCell.append(detailButton);
    row.append(actionCell);
    return row;
  });
  elements.body.replaceChildren(...rows);
}

function renderPagination(pagination) {
  state.totalPages = Math.max(1, Number(pagination.total_pages) || 1);
  elements.count.textContent = `共 ${pagination.total} 条 · 最多显示最近 500 条`;
  elements.pageSummary.textContent = `第 ${pagination.page} 页，每页 ${pagination.page_size} 条`;
  elements.pageIndicator.textContent = `${pagination.page} / ${state.totalPages}`;
  elements.previous.disabled = pagination.page <= 1;
  elements.next.disabled = pagination.page >= state.totalPages;
}

async function loadRecords() {
  elements.error.hidden = true;
  elements.error.textContent = "";
  showEmptyMessage("正在读取记录…");
  const params = currentFilters();
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  try {
    const response = await fetch(`/api/events?${params.toString()}`, { headers: { Accept: "application/json" } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `请求失败（${response.status}）`);
    renderRows(body.items);
    renderPagination(body.pagination);
  } catch (error) {
    showEmptyMessage("记录读取失败");
    elements.error.textContent = error.message;
    elements.error.hidden = false;
  }
}

async function loadDetail(eventId) {
  elements.details.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "muted";
  loading.textContent = "正在读取详情…";
  elements.details.append(loading);
  elements.dialog.showModal();
  try {
    const response = await fetch(`/api/events/${encodeURIComponent(eventId)}`, { headers: { Accept: "application/json" } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `请求失败（${response.status}）`);
    const entries = Object.entries(body).map(([key, value]) => {
      const wrapper = document.createElement("div");
      wrapper.className = "detail-item";
      const term = document.createElement("dt");
      term.textContent = detailNames[key] || key;
      const description = document.createElement("dd");
      if (key === "timestamp_ms") description.textContent = formatLocalTime(value);
      else if (key === "class_id") description.textContent = classNames[value] || value || "—";
      else if (key === "confidence" && value !== null) description.textContent = `${(Number(value) * 100).toFixed(1)}%`;
      else description.textContent = value === null || value === "" ? "—" : String(value);
      wrapper.append(term, description);
      return wrapper;
    });
    elements.details.replaceChildren(...entries);
  } catch (error) {
    const message = document.createElement("p");
    message.className = "error-message";
    message.textContent = error.message;
    elements.details.replaceChildren(message);
  }
}

document.querySelector("#apply-filters").addEventListener("click", () => {
  state.page = 1;
  loadRecords();
});
document.querySelector("#reset-filters").addEventListener("click", () => {
  elements.date.value = "";
  elements.classId.value = "";
  elements.result.value = "";
  state.page = 1;
  loadRecords();
});
elements.pageSize.addEventListener("change", () => {
  state.pageSize = Number(elements.pageSize.value);
  state.page = 1;
  loadRecords();
});
elements.previous.addEventListener("click", () => {
  if (state.page > 1) { state.page -= 1; loadRecords(); }
});
elements.next.addEventListener("click", () => {
  if (state.page < state.totalPages) { state.page += 1; loadRecords(); }
});
document.querySelector("#export-csv").addEventListener("click", () => {
  window.location.assign(`/api/events/export?${currentFilters().toString()}`);
});
document.querySelector("#close-dialog").addEventListener("click", () => elements.dialog.close());
elements.dialog.addEventListener("click", (event) => {
  if (event.target === elements.dialog) elements.dialog.close();
});

const today = new Date();
elements.date.value = [today.getFullYear(), String(today.getMonth() + 1).padStart(2, "0"), String(today.getDate()).padStart(2, "0")].join("-");
loadRecords();