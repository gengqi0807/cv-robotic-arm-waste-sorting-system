import { createMonitor } from "./workbench-monitor.js";

const taskNames = {
  idle: "空闲", running: "运行中", paused: "已暂停", finished: "已结束", fault: "故障", estop: "急停",
};
const robotNames = {
  idle: "空闲", running: "运行中", paused: "已暂停", fault: "故障", estop: "急停",
};
const commandStates = {
  start: ["idle", "finished"], pause: ["running"], resume: ["paused"], finish: ["running", "paused"],
};
const nextStates = {
  start: ["running", "running"], pause: ["paused", "paused"], resume: ["running", "running"],
  finish: ["finished", "idle"],
};
const timeFormat = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  hour12: false,
});
const formatTime = (value) => Number(value) > 0 ? timeFormat.format(Number(value)) : "—";
const select = (id) => document.getElementById(id);
const modeInputs = Array.from(document.querySelectorAll('input[name="workbench-mode"]'));
const commandButtons = document.querySelectorAll(".task-command");
const actionButtons = document.querySelectorAll(".action-card");
const statusPill = select("task-state-pill");
const deviceStatus = select("device-status");
const taskAlert = select("task-alert");
const estopNotice = select("estop-notice");
const taskError = select("task-error");
const actionError = select("action-error");
const actionLog = select("action-log");
const monitor = createMonitor(formatTime);

let mode;
let view;
let state = {};
let online = false;
let readingState = false;
let pending = false;
let revision = 0;

function showMessage(element, text = "") {
  if (!element) return;
  element.hidden = !text;
  if (element.textContent !== text) element.textContent = text;
}

function updateControls() {
  const locked = [state.task_state, state.robot_state].some((value) => value === "fault" || value === "estop");
  const ready = (mode === "mock" || online) && !locked && !pending;
  for (const button of commandButtons) {
    button.disabled = !ready || !commandStates[button.dataset.command].includes(state.task_state);
  }
  for (const button of actionButtons) {
    button.disabled = !ready || mode !== "live";
  }
  for (const input of modeInputs) input.disabled = pending;
  if (actionLog) {
    select("action-availability").textContent = mode === "mock" ? "切到现场联调后可下发课程动作。"
      : !online ? "等待设备连接。" : locked ? "请先处理设备告警。" : "动作将发送到当前设备。";
  }
}

function renderState() {
  const mock = mode === "mock";
  const taskState = state.task_state;
  const robotState = state.robot_state;
  const estopped = taskState === "estop" || robotState === "estop";
  statusPill.className = "state-pill" + (taskNames[taskState] ? " state-" + taskState : "");
  statusPill.textContent = taskNames[taskState] || "—";
  select("current-robot-state").textContent = online || mock || estopped ? robotNames[robotState] || "—" : "—";
  select("current-task-id").textContent = state.task_id || "—";
  select("last-updated").textContent = formatTime(state.timestamp_ms);
  deviceStatus.textContent = mock ? "模拟模式" : online ? "设备在线" : readingState ? "正在连接" : "设备离线";
  deviceStatus.classList.toggle("live", !mock && online);
  estopNotice.hidden = !estopped;

  const alertText = state.transport_error || state.message || "";
  taskAlert.hidden = !alertText;
  taskAlert.classList.toggle("alert-box-quiet", (online || mock) && !estopped && taskState !== "fault" && robotState !== "fault");
  select("alert-title").textContent = state.transport_error ? "状态更新失败" : "设备提示";
  select("alert-message").textContent = alertText;
  select("alert-code").textContent = state.code ? "问题编号 " + state.code : "";
  monitor.setOnline(online);
  updateControls();
}

async function requestJSON(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.accepted === false) {
    throw new Error(result.message || result.error || "HTTP " + response.status);
  }
  return result;
}

async function pollState(signal) {
  if (signal.aborted) return;
  const expectedRevision = revision;
  try {
    const snapshot = await requestJSON("/api/tasks/state", { signal });
    if (signal.aborted || expectedRevision !== revision) return;
    readingState = false;
    online = snapshot.device_online === true;
    state = snapshot;
    monitor.showBoardDetections(online ? snapshot.detections : []);
    renderState();
  } catch (error) {
    if (signal.aborted || expectedRevision !== revision) return;
    readingState = false;
    online = false;
    state = { ...state, transport_error: error.message };
    monitor.showBoardDetections([]);
    renderState();
  } finally {
    if (!signal.aborted) window.setTimeout(() => pollState(signal), 1000);
  }
}

function switchMode(value) {
  if (mode === value || pending) return;
  view?.abort();
  view = new AbortController();
  revision += 1;
  mode = value;
  online = false;
  readingState = value === "live";
  state = readingState ? {} : { task_state: "idle", robot_state: "idle", timestamp_ms: Date.now() };
  showMessage(taskError);
  showMessage(actionError);
  if (actionLog) showMessage(actionLog);
  if (select("control-config")) select("control-config").hidden = !readingState;
  monitor.start(mode, view.signal);
  renderState();
  if (readingState) pollState(view.signal);
}

async function postCommand(url, payload, completed, errorElement) {
  pending = true;
  updateControls();
  showMessage(errorElement);
  try {
    const result = await requestJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    revision += 1;
    state = result;
    online = result.device_online === true;
    renderState();
    completed?.(result);
  } catch (error) {
    showMessage(errorElement, error.message);
  } finally {
    pending = false;
    updateControls();
  }
}

async function sendCommand(button) {
  if (button.disabled) return;
  const command = button.dataset.command;
  if (mode === "mock") {
    const [taskState, robotState] = nextStates[command];
    const timestamp = Date.now();
    state = {
      task_state: taskState, robot_state: robotState, timestamp_ms: timestamp,
      task_id: command === "start" ? "demo-" + timestamp : state.task_id,
    };
    renderState();
    return;
  }
  const payload = {
    command,
    task_id: state.task_id || null,
    task_mode: select("task-mode").value,
    request_id: globalThis.crypto?.randomUUID?.() || "req-" + Date.now() + "-" + Math.random().toString(16).slice(2),
    timestamp_ms: Date.now(),
  };
  const operator = select("operator-id");
  if (operator) {
    payload.operator_id = operator.value.trim();
    payload.session_id = select("session-id").value.trim();
    if (!payload.operator_id || !payload.session_id) {
      showMessage(taskError, "请填写操作员编号和会话编号。");
      return;
    }
  }
  await postCommand("/api/tasks/control", payload, null, taskError);
}

for (const button of commandButtons) button.addEventListener("click", () => sendCommand(button));
for (const button of actionButtons) {
  button.addEventListener("click", () => {
    if (button.disabled || mode !== "live") return;
    postCommand("/api/tasks/action", { action: button.dataset.action, args: {} }, (result) => {
      const name = button.querySelector("strong").textContent;
      showMessage(actionLog, "已排队 · " + name + " · #" + result.command_seq + "\n" + actionLog.textContent);
    }, actionError);
  });
}
for (const input of modeInputs) {
  input.addEventListener("change", () => { if (input.checked) switchMode(input.value); });
}
switchMode(modeInputs.find((input) => input.checked).value);
