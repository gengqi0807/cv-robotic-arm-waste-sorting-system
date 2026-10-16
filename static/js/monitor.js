"use strict";

const classNames = {
  recyclable: "可回收物",
  kitchen: "厨余垃圾",
  hazardous: "有害垃圾",
  other: "其他垃圾",
};
const robotNames = { idle: "空闲", running: "运行", paused: "暂停", fault: "故障", estop: "急停" };
const elements = {
  modePill: document.querySelector("#monitor-mode-pill"),
  modeDescription: document.querySelector("#monitor-mode-description"),
  connection: document.querySelector("#monitor-connection"),
  wsUrl: document.querySelector("#monitor-ws-url"),
  wsState: document.querySelector("#monitor-ws-state"),
  imageUrl: document.querySelector("#monitor-image-url"),
  image: document.querySelector("#camera-image"),
  scene: document.querySelector("#camera-scene"),
  cameraCaption: document.querySelector("#camera-caption"),
  cameraSource: document.querySelector("#camera-source"),
  cameraSourceDetail: document.querySelector("#camera-source-detail"),
  cameraLiveLabel: document.querySelector("#camera-live-label"),
  cameraTimestamp: document.querySelector("#camera-timestamp"),
  frameId: document.querySelector("#frame-id"),
  detectSource: document.querySelector("#detect-source"),
  motionSource: document.querySelector("#motion-source"),
  robotPill: document.querySelector("#robot-state-pill"),
  robotState: document.querySelector("#robot-state-text"),
  robotCode: document.querySelector("#robot-code"),
  robotRecoverable: document.querySelector("#robot-recoverable"),
  robotUpdated: document.querySelector("#robot-updated"),
  robotMessage: document.querySelector("#robot-message"),
  error: document.querySelector("#monitor-error"),
};

let monitorMode = "mock";
let monitorSocket = null;
let demoFrame = 1;

function localTime(timestampMs = Date.now()) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date(Number(timestampMs)));
}

function setState(value) {
  const state = robotNames[value] ? value : "fault";
  elements.robotState.textContent = robotNames[state];
  elements.robotPill.className = `state-pill ${state === "idle" ? "" : `state-${state}`}`;
  elements.robotPill.textContent = robotNames[state];
}

function updateDetection(data, source = "实时 detect 消息") {
  const detections = Array.isArray(data.detections) ? data.detections : [];
  const detection = detections[0] || data.detection || data;
  if (detection.class_id) document.querySelector("#detected-class").textContent = classNames[detection.class_id] || detection.class_name || detection.class_id;
  else if (detection.class_name) document.querySelector("#detected-class").textContent = detection.class_name;
  if (detection.confidence !== undefined) document.querySelector("#detected-confidence").textContent = Number(detection.confidence).toFixed(3);
  if (detection.u_px !== undefined || detection.v_px !== undefined) {
    document.querySelector("#detected-pixel").textContent = `${detection.u_px ?? "—"} px, ${detection.v_px ?? "—"} px`;
    document.querySelector("#scene-coordinate").textContent = `u: ${detection.u_px ?? "—"} px    v: ${detection.v_px ?? "—"} px`;
  }
  if (detection.bbox_px) document.querySelector("#detected-box").textContent = Array.isArray(detection.bbox_px) ? `[${detection.bbox_px.join(", ")}]` : JSON.stringify(detection.bbox_px);
  if (data.frame_id) {
    document.querySelector("#detected-frame").textContent = data.frame_id;
    elements.frameId.textContent = data.frame_id;
  }
  if (data.timestamp_ms) elements.cameraTimestamp.textContent = localTime(data.timestamp_ms);
  const inferenceMs = data.inference_ms ?? data["推理_ms"];
  if (inferenceMs !== undefined) document.querySelector("#detected-duration").textContent = `${inferenceMs} ms`;
  elements.detectSource.textContent = source;
}

function updateTransform(data) {
  const x = data.x_m ?? data.target_x_m;
  const y = data.y_m ?? data.target_y_m;
  const z = data.z_m ?? data.target_z_m;
  if (x !== undefined || y !== undefined || z !== undefined) {
    document.querySelector("#target-position").textContent = `${x ?? "—"} m, ${y ?? "—"} m, ${z ?? "—"} m`;
  }
  if (data.reachable !== undefined) document.querySelector("#target-reachable").textContent = data.reachable ? "可达" : "不可达";
  if (data.calibration_id) document.querySelector("#target-calibration").textContent = data.calibration_id;
  if (data.error_code) {
    elements.error.textContent = `坐标转换异常：${data.error_code}`;
    elements.error.hidden = false;
  }
  elements.motionSource.textContent = "实时 transform 消息";
}

function updateExecution(data) {
  if (data.accepted !== undefined) document.querySelector("#execute-accepted").textContent = data.accepted ? "是" : "否";
  if (data.result !== undefined) document.querySelector("#execute-result").textContent = String(data.result);
  if (data.duration_ms !== undefined) document.querySelector("#execute-duration").textContent = `${data.duration_ms} ms`;
  if (data.error_code) {
    elements.error.textContent = `机械臂执行异常：${data.error_code}`;
    elements.error.hidden = false;
  }
  elements.motionSource.textContent = "实时 execute_pick 消息";
}

function updateRobotState(data) {
  if (data.robot_state) setState(data.robot_state);
  elements.robotCode.textContent = data.code || "—";
  elements.robotRecoverable.textContent = data.recoverable === undefined ? "—" : data.recoverable ? "是" : "否";
  elements.robotUpdated.textContent = localTime(data.timestamp_ms || Date.now());
  const message = data.message || "暂无设备告警。";
  const strong = document.createElement("strong");
  strong.textContent = data.code ? `设备提示 · ${data.code}` : "设备提示";
  const paragraph = document.createElement("p");
  paragraph.textContent = message;
  elements.robotMessage.replaceChildren(strong, paragraph);
}

function applyMessage(message) {
  const type = message.type || message.event || message.interface || "";
  const data = message.data || message.payload || message;
  if (type === "detect" || data.detections) updateDetection(data);
  else if (type === "transform" || data.reachable !== undefined) updateTransform(data);
  else if (type === "execute_pick" || data.accepted !== undefined || data.duration_ms !== undefined) updateExecution(data);
  else if (["status", "alarm", "state", "status_alarm"].includes(type) || data.robot_state) updateRobotState(data);
  else throw new Error("未识别的状态消息类型；适配格式为 {type, data}。 ");
  elements.cameraLiveLabel.textContent = "实时消息";
  elements.cameraSource.textContent = "接口消息";
  elements.cameraSourceDetail.textContent = "WebSocket 适配消息";
}

function mockTick() {
  if (monitorMode !== "mock") return;
  demoFrame += 1;
  const timestamp = Date.now();
  const classes = ["recyclable", "kitchen", "hazardous", "other"];
  const classId = classes[(demoFrame - 1) % classes.length];
  const u = 304 + (demoFrame % 9) * 4;
  const v = 232 + (demoFrame % 5) * 3;
  updateDetection({
    frame_id: `frame_demo_${String(demoFrame).padStart(3, "0")}`,
    timestamp_ms: timestamp,
    inference_ms: 38 + (demoFrame % 11),
    detections: [{ class_id: classId, confidence: 0.91 + (demoFrame % 7) / 100, u_px: u, v_px: v, bbox_px: [u - 65, v - 50, u + 65, v + 50] }],
  }, "模拟 detect");
  elements.cameraSource.textContent = "模拟画面";
  elements.cameraSourceDetail.textContent = "CSS 模拟场景";
  elements.cameraTimestamp.textContent = localTime(timestamp);
}

function connectSocket() {
  const address = elements.wsUrl.value.trim();
  if (!address) {
    elements.wsState.textContent = "请填写 WebSocket 地址。";
    return;
  }
  if (monitorSocket) monitorSocket.close();
  elements.wsState.textContent = "正在连接…";
  try {
    monitorSocket = new WebSocket(address);
    monitorSocket.addEventListener("open", () => { elements.wsState.textContent = "状态流已连接"; });
    monitorSocket.addEventListener("message", (event) => {
      try {
        applyMessage(JSON.parse(event.data));
        elements.error.hidden = true;
      } catch (error) {
        elements.error.textContent = `状态消息处理失败：${error.message}`;
        elements.error.hidden = false;
      }
    });
    monitorSocket.addEventListener("close", () => { elements.wsState.textContent = "状态流已断开"; });
    monitorSocket.addEventListener("error", () => { elements.wsState.textContent = "连接失败，请检查地址及服务端适配。"; });
  } catch (error) {
    elements.wsState.textContent = `连接失败：${error.message}`;
  }
}

function loadImage() {
  const address = elements.imageUrl.value.trim();
  if (!address) return;
  elements.image.hidden = false;
  elements.scene.hidden = true;
  elements.image.src = address;
  elements.image.onload = () => {
    elements.cameraCaption.textContent = "显示配置的最近图片地址 · 非视频流";
    elements.cameraSource.textContent = "外部图片";
    elements.cameraSourceDetail.textContent = "配置的图片 URL";
  };
  elements.image.onerror = () => {
    elements.error.textContent = "最近图片加载失败，请检查图片地址及跨域策略。";
    elements.error.hidden = false;
    elements.image.hidden = true;
    elements.scene.hidden = false;
  };
}

document.querySelectorAll('input[name="monitor-mode"]').forEach((input) => {
  input.addEventListener("change", () => {
    if (!input.checked) return;
    monitorMode = input.value;
    const live = monitorMode === "live";
    elements.modePill.textContent = live ? "接口联调" : "模拟数据";
    elements.modePill.classList.toggle("live", live);
    elements.modeDescription.textContent = live
      ? "模拟刷新已停止；等待真实消息或配置的最近图片。"
      : "画面、识别、坐标和执行状态均为本地演示值。";
    elements.connection.hidden = !live;
    if (!live) {
      if (monitorSocket) monitorSocket.close();
      monitorSocket = null;
      elements.wsState.textContent = "未连接";
      elements.scene.hidden = false;
      elements.image.hidden = true;
      elements.image.removeAttribute("src");
      elements.cameraCaption.textContent = "模拟工作台画面 · 非真实相机视频";
    }
  });
});
document.querySelector("#connect-monitor").addEventListener("click", connectSocket);
document.querySelector("#load-monitor-image").addEventListener("click", loadImage);
updateRobotState({ robot_state: "running", message: "模拟分拣流程运行中。", recoverable: true, timestamp_ms: Date.now() });
mockTick();
window.setInterval(mockTick, 3000);