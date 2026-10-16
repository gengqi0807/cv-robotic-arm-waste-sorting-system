"use strict";

const classNames = {
  recyclable: "可回收物",
  kitchen: "厨余垃圾",
  hazardous: "有害垃圾",
  other: "其他垃圾",
};
const classColors = {
  recyclable: "#27785d",
  kitchen: "#c86a35",
  hazardous: "#a9423d",
  other: "#657c91",
};
const elements = {
  dateField: document.querySelector("#date-field"),
  date: document.querySelector("#stats-date"),
  shiftFields: document.querySelector("#shift-fields"),
  shiftStart: document.querySelector("#shift-start"),
  shiftEnd: document.querySelector("#shift-end"),
  caption: document.querySelector("#range-caption"),
  total: document.querySelector("#total-count"),
  success: document.querySelector("#success-count"),
  failure: document.querySelector("#failure-count"),
  fault: document.querySelector("#fault-count"),
  categories: document.querySelector("#category-body"),
  error: document.querySelector("#stats-error"),
};

function toLocalInputValue(value) {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function localDateRange(dateValue) {
  if (!dateValue) throw new Error("请选择统计日期。");
  const [year, month, day] = dateValue.split("-").map(Number);
  const start = new Date(year, month - 1, day);
  const end = new Date(year, month - 1, day + 1);
  return { start_time_ms: start.getTime(), end_time_ms: end.getTime() };
}

function selectedRange() {
  const mode = document.querySelector('input[name="range-mode"]:checked').value;
  if (mode === "date") return { mode, ...localDateRange(elements.date.value) };
  if (!elements.shiftStart.value || !elements.shiftEnd.value) throw new Error("请填写班次开始和结束时间。");
  const start = new Date(elements.shiftStart.value).getTime();
  const end = new Date(elements.shiftEnd.value).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) throw new Error("结束时间必须晚于开始时间。");
  return { mode, start_time_ms: start, end_time_ms: end };
}

function showCategoryRows(categories) {
  const rows = Object.entries(classNames).map(([classId, label]) => {
    const category = categories[classId] || { count: 0, ratio: 0 };
    const ratio = Math.max(0, Math.min(100, Number(category.ratio || 0) * 100));
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    const name = document.createElement("span");
    name.className = "category-name";
    name.style.setProperty("--swatch", classColors[classId]);
    const swatch = document.createElement("span");
    swatch.className = "category-swatch";
    const text = document.createElement("span");
    text.textContent = label;
    name.append(swatch, text);
    nameCell.append(name);

    const countCell = document.createElement("td");
    countCell.className = "category-count";
    countCell.textContent = Number(category.count || 0).toLocaleString("zh-CN");
    const ratioCell = document.createElement("td");
    ratioCell.className = "ratio-value";
    ratioCell.textContent = `${ratio.toFixed(1)}%`;
    const barCell = document.createElement("td");
    const track = document.createElement("div");
    track.className = "ratio-track";
    track.setAttribute("aria-label", `${label}占比${ratio.toFixed(1)}%`);
    const fill = document.createElement("div");
    fill.className = "ratio-fill";
    fill.style.setProperty("--ratio", `${ratio}%`);
    fill.style.setProperty("--swatch", classColors[classId]);
    track.append(fill);
    barCell.append(track);
    row.append(nameCell, countCell, ratioCell, barCell);
    return row;
  });
  elements.categories.replaceChildren(...rows);
}

function formatLocalRange(startMs, endMs) {
  const formatter = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
  return `${formatter.format(new Date(startMs))} 至 ${formatter.format(new Date(endMs))}`;
}

async function loadStats() {
  elements.error.hidden = true;
  elements.error.textContent = "";
  try {
    const range = selectedRange();
    const params = new URLSearchParams({
      start_time_ms: String(range.start_time_ms),
      end_time_ms: String(range.end_time_ms),
    });
    elements.caption.textContent = `统计区间：${formatLocalRange(range.start_time_ms, range.end_time_ms)}（本地时间）`;
    const response = await fetch(`/api/stats?${params.toString()}`, { headers: { Accept: "application/json" } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `请求失败（${response.status}）`);
    elements.total.textContent = Number(body.total_count || 0).toLocaleString("zh-CN");
    elements.success.textContent = Number(body.success_count || 0).toLocaleString("zh-CN");
    elements.failure.textContent = Number(body.failure_count || 0).toLocaleString("zh-CN");
    elements.fault.textContent = Number(body.fault_count || 0).toLocaleString("zh-CN");
    showCategoryRows(body.categories || {});
  } catch (error) {
    elements.error.textContent = error.message;
    elements.error.hidden = false;
  }
}

document.querySelectorAll('input[name="range-mode"]').forEach((input) => {
  input.addEventListener("change", () => {
    const isDateMode = input.value === "date" && input.checked;
    elements.dateField.hidden = !isDateMode;
    elements.shiftFields.hidden = isDateMode;
  });
});
document.querySelector("#load-stats").addEventListener("click", loadStats);

const now = new Date();
elements.date.value = [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("-");
const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
elements.shiftStart.value = toLocalInputValue(midnight);
elements.shiftEnd.value = toLocalInputValue(now);
loadStats();