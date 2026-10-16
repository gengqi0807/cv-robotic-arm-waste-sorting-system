"use strict";

const taskStates = {
  idle: "空闲",
  running: "运行中",
  paused: "已暂停",
  finished: "已结束",
  fault: "故障",
  estop: "急停",
};
const robotStates = {
  idle: "空闲",
  running: "运行",
  paused: "暂停",
  fault: "故障",
  estop: "急停",
};
const elements = {
  modePill: document.querySelector("#mode-pill"),
  modeDescription: document.querySelector("#mode-description"),
  sessionBadge: document.querySelector("#session-badge"),
  operatorId: document.querySelector("#operator-id"),
  sessionId: document.querySelector("#session-id"),
  taskMode: document.querySelector("#task-mode"),
  controlConfig: document.querySelector("#control-config"),
  controlUrl: document.querySelector("#control-url"),
  wsUrl: document.querySelector("#control-ws-url"),
  wsState: document.querySelector("#control-ws-state"),
  stateSource: document.querySelector("#state-source"),
  statePill: document.querySelector("#task-state-pill"),
  taskId: document.querySelector("#current-task-id"),
  taskState: document.querySelector("#current-task-state"),
  robotState: document.querySelector("#current-robot-state"),
  updated: document.querySelector("#last-updated"),
  alert: document.querySelector("#task-alert"),
  alertTitle: document.querySelector("#alert-title"),
  alertMessage: document.querySelector("#alert-message"),
  alertCode: document.querySelector("#alert-code"),
  estop: document.querySelector("#estop-notice"),
  safety: document.querySelector("#safety-note"),
  error: document.querySelector("#task-error"),
};

let controlMode = "mock";
let currentTaskId = "";
let taskState = "idle";
let robotState = "idle";
let stateSocket = null;

function localTime(timestampMs = Date.now()) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date(Number(timestampMs)));
}

function setStatePill(state) {
  const knownState = taskStates[state] ? state : "fault";
  elements.statePill.className = `state-pill ${knownState === "idle" ? "" : `state-${knownState}`}`;
  elements.statePill.textContent = taskStates[knownState];
}

function updateButtons() {
  const estopped = taskState === "estop" || robotState === "estop";
  const allowed = {
    start: !estopped && ["idle", "finished"].includes(taskState),
    pause: !estopped && taskState === "running",
    resume: !estopped && taskState === "paused",
    finish: !estopped && ["running", "paused"].includes(taskState),
  };
  document.querySelectorAll(".task-command").forEach((button) => {
    button.disabled = !allowed[button.dataset.command];
  });
  elements.estop.hidden = !estopped;
  if (estopped) elements.safety.textContent = "急停状态已锁定。普通网页命令全部禁用，请检查设备并由人员执行人工复位。";
  else elements.safety.textContent = "实体急停优先于网页控制。网页命令不能清除急停状态。";
}

function showAlert({ code = "", message = "", recoverable = false } = {}) {
  const hasAlert = Boolean(code || message);
  elements.alert.hidden = !hasAlert;
  if (!hasAlert) return;
  elements.alertTitle.textContent = recoverable ? "设备提示 · 可恢复" : "设备告警";
  elements.alertMessage.textContent = message || "设备报告异常状态，请现场检查。";
  elements.alertCode.textContent = code ? `问题编号：${code}` : "";
}

function applyState(data, source = "WebSocket 状态消息") {
  if (data.task_id) currentTaskId = data.task_id;
  if (data.task_state && taskStates[data.task_state]) taskState = data.task_state;
  if (data.robot_state && robotStates[data.robot_state]) robotState = data.robot_state;
  elements.taskId.textContent = currentTaskId || "尚未启动";
  elements.taskState.textContent = taskState;
  elements.robotState.textContent = robotStates[robotState] || robotState;
  elements.updated.textContent = localTime(data.timestamp_ms || Date.now());
  elements.stateSource.textContent = source;
  setStatePill(taskState);
  showAlert({ code: data.code || "", message: data.message || "", recoverable: Boolean(data.recoverable) });
  updateButtons();
}

function mockCommand(command) {
  const nowMs = Date.now();
  if (command === "start") {
    currentTaskId = `demo-${nowMs}`;
    taskState = "running";
    robotState = "running";
  } else if (command === "pause") {
    taskState = "paused";
    robotState = "paused";
  } else if (command === "resume") {
    taskState = "running";
    robotState = "running";
  } else if (command === "finish") {
    taskState = "finished";
    robotState = "idle";
  }
  applyState({ task_id: currentTaskId, task_state: taskState, robot_state: robotState, timestamp_ms: nowMs }, "本地模拟状态");
  elements.error.hidden = true;
}

async function sendCommand(command) {
  elements.error.hidden = true;
  if (controlMode === "mock") {
    mockCommand(command);
    return;
  }
  if (taskState === "estop" || robotState === "estop") {
    updateButtons();
    return;
  }

  const requestId = globalThis.crypto?.randomUUID?.() || `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const payload = {
    command,
    task_id: currentTaskId || null,
    task_mode: elements.taskMode.value,
    operator_id: elements.operatorId.value.trim(),
    session_id: elements.sessionId.value.trim(),
    request_id: requestId,
    timestamp_ms: Date.now(),
  };
  if (!payload.operator_id || !payload.session_id) {
    elements.error.textContent = "请填写操作员编号和会话编号。";
    elements.error.hidden = false;
    return;
  }

  document.querySelectorAll(".task-command").forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(elements.controlUrl.value.trim() || "/api/tasks/control", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.message || body.error || `控制请求失败（HTTP ${response.status}）`);
    if (!body.accepted) throw new Error(body.message || body.reason_code || "任务命令未被接受。");
    if (body.task_id) currentTaskId = body.task_id;
    applyState({
      task_id: body.task_id || currentTaskId,
      task_state: body.task_state,
      timestamp_ms: body.timestamp_ms,
      message: body.message,
    }, "任务控制接口响应");
    elements.stateSource.textContent = "等待机械臂状态 WebSocket 确认";
  } catch (error) {
    elements.error.textContent = `${error.message}。当前服务尚未接入任务控制接口；页面未模拟成功。`;
    elements.error.hidden = false;
    updateButtons();
  }
}

function connectStateSocket() {
  const address = elements.wsUrl.value.trim();
  if (!address) {
    elements.wsState.textContent = "请填写 WebSocket 地址。";
    return;
  }
  if (stateSocket) stateSocket.close();
  elements.wsState.textContent = "正在连接…";
  try {
    stateSocket = new WebSocket(address);
    stateSocket.addEventListener("open", () => { elements.wsState.textContent = "状态流已连接"; });
    stateSocket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(event.data);
        applyState(message.data || message, "WebSocket 实时状态");
      } catch {
        elements.wsState.textContent = "状态消息不是有效 JSON。";
      }
    });
    stateSocket.addEventListener("close", () => { elements.wsState.textContent = "状态流已断开"; });
    stateSocket.addEventListener("error", () => { elements.wsState.textContent = "连接失败，请检查地址和服务端适配。"; });
  } catch (error) {
    elements.wsState.textContent = `连接失败：${error.message}`;
  }
}

document.querySelectorAll('input[name="control-mode"]').forEach((input) => {
  input.addEventListener("change", () => {
    if (!input.checked) return;
    controlMode = input.value;
    const live = controlMode === "live";
    elements.modePill.textContent = live ? "接口联调" : "模拟模式";
    elements.modePill.classList.toggle("live", live);
    elements.modeDescription.textContent = live
      ? "命令会请求配置的任务控制地址；当前服务未实现该接口，失败不会伪装成功。"
      : "仅演示页面状态，不会向机械臂发送命令。";
    elements.sessionBadge.textContent = live ? "需有效操作员会话" : "演示身份";
    elements.controlConfig.hidden = !live;
    elements.stateSource.textContent = live ? "等待任务接口或 WebSocket 消息" : "本地模拟状态";
    if (!live && stateSocket) {
      stateSocket.close();
      stateSocket = null;
      elements.wsState.textContent = "WebSocket 未连接";
    }
  });
});
document.querySelectorAll(".task-command").forEach((button) => {
  button.addEventListener("click", () => sendCommand(button.dataset.command));
});
document.querySelector("#connect-control-ws").addEventListener("click", connectStateSocket);
applyState({ task_state: "idle", robot_state: "idle", timestamp_ms: Date.now() }, "本地模拟状态");