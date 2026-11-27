const categories = { recyclable: "可回收物", kitchen: "厨余垃圾", hazardous: "有害垃圾", other: "其他垃圾" };
const colors = { red: "hazardous", blue: "recyclable", green: "kitchen", yellow: "other" };
const simulatedClasses = Object.keys(categories);

export function createMonitor(formatTime) {
  const select = (id) => document.getElementById(id);
  const show = (id, value) => {
    const element = select(id);
    const text = value === undefined || value === null || value === "" ? "—" : String(value);
    if (element.textContent !== text) element.textContent = text;
  };
  const message = (id, text = "") => {
    select(id).hidden = !text;
    show(id, text);
  };
  const image = select("camera-image");
  const scene = select("camera-scene");
  const placeholder = select("camera-placeholder");
  let online = false;
  let frameKey;
  let receivedFrameAt;
  let objectUrl;
  let imageSource;
  let fallbackUrl;
  let socket;
  let auxiliaryDetectionAt;
  let demoFrame = 0;

  function showDetection(data, source) {
    const raw = data.detections?.[0] ?? data.detection ?? data;
    const detection = typeof raw === "string" ? { class_id: raw } : raw;
    const id = detection.class_id || detection.class_name;
    const key = typeof id === "string" ? id.toLowerCase() : "";
    const { u_px: u, v_px: v, confidence, bbox_px: box } = detection;
    const duration = data.inference_ms ?? data["推理_ms"];
    show("detected-class", categories[colors[key] || key] || id);
    show("detected-confidence", typeof confidence === "number" ? confidence.toFixed(3) : null);
    show("detected-pixel", u != null || v != null ? (u ?? "—") + " px, " + (v ?? "—") + " px" : null);
    show("detected-box", Array.isArray(box) ? "[" + box.join(", ") + "]" : null);
    show("detected-frame", data.frame_id);
    show("detected-duration", duration == null ? null : duration + " ms");
    show("detect-source", source);
    if (u != null && v != null) show("scene-coordinate", "u: " + u + " px   v: " + v + " px");
  }

  function showTransform(data) {
    const x = data.x_m ?? data.target_x_m;
    const y = data.y_m ?? data.target_y_m;
    const z = data.z_m ?? data.target_z_m;
    const coords = [x, y, z];
    show("target-position", coords.some((value) => value != null) ? coords.map((value) => value == null ? "—" : value + " m").join(", ") : null);
    show("target-reachable", data.reachable == null ? null : data.reachable ? "可达" : "不可达");
    show("target-calibration", data.calibration_id);
  }

  function showExecution(data) {
    show("execute-accepted", data.accepted == null ? null : data.accepted ? "是" : "否");
    show("execute-result", data.result);
    show("execute-duration", data.duration_ms == null ? null : data.duration_ms + " ms");
  }

  function showImage(url, source) {
    imageSource = source;
    image.hidden = !url;
    scene.hidden = true;
    placeholder.hidden = Boolean(url);
    if (url) image.src = url;
    else image.removeAttribute("src");
  }

  function updateCameraLabel() {
    if (imageSource !== "board") return;
    const fresh = online && performance.now() - receivedFrameAt < 5000;
    show("camera-live-label", fresh ? "实时画面" : "最近画面");
    show("camera-source", fresh ? "实时相机" : "最近相机帧");
    show("camera-caption", fresh ? "机械臂端上传画面" : "当前显示最后收到的相机画面");
    select("camera-source").classList.toggle("live", fresh);
  }

  function showFallback() {
    if (!fallbackUrl || imageSource === "fallback") return;
    showImage(fallbackUrl, "fallback");
    show("camera-live-label", "备用静态图");
    show("camera-source", "备用图片");
    show("camera-source-detail", "外部图片");
    show("camera-caption", "显示手动设置的备用图片");
    show("camera-timestamp", null);
    show("frame-id", null);
    select("camera-source").classList.remove("live");
  }

  async function pollFrame(signal) {
    if (signal.aborted) return;
    try {
      const response = await fetch("/api/robot/frame", { cache: "no-store", signal });
      if (signal.aborted) return;
      if (response.status === 204) {
        showFallback();
        return;
      }
      if (!response.ok) throw new Error("HTTP " + response.status);
      const frameId = response.headers.get("X-Frame-Id");
      const timestamp = response.headers.get("X-Frame-Timestamp-Ms");
      const key = frameId || timestamp ? (frameId || "") + "/" + (timestamp || "") : undefined;
      const blob = await response.blob();
      if (signal.aborted) return;
      if (!key || key !== frameKey || imageSource !== "board") {
        const previousUrl = objectUrl;
        objectUrl = URL.createObjectURL(blob);
        showImage(objectUrl, "board");
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        frameKey = key;
        receivedFrameAt = performance.now();
        show("camera-source-detail", "板端相机");
        show("camera-timestamp", formatTime(timestamp));
        show("frame-id", frameId);
      }
      message("camera-error");
      updateCameraLabel();
    } catch (error) {
      if (!signal.aborted) {
        message("camera-error", "读取相机画面失败 · " + error.message);
        receivedFrameAt = undefined;
        updateCameraLabel();
        showFallback();
      }
    } finally {
      if (!signal.aborted) window.setTimeout(() => pollFrame(signal), 500);
    }
  }

  function renderDemo(signal) {
    if (signal.aborted) return;
    demoFrame += 1;
    const u = 304 + demoFrame % 9 * 4;
    const v = 232 + demoFrame % 5 * 3;
    const frameId = "frame_demo_" + String(demoFrame).padStart(3, "0");
    showDetection({
      frame_id: frameId, inference_ms: 38 + demoFrame % 11,
      detections: [{ class_id: simulatedClasses[(demoFrame - 1) % simulatedClasses.length],
        confidence: 0.91 + demoFrame % 7 / 100, u_px: u, v_px: v, bbox_px: [u - 65, v - 50, u + 65, v + 50] }],
    }, "模拟识别");
    show("frame-id", frameId);
    show("camera-timestamp", formatTime(Date.now()));
    window.setTimeout(() => renderDemo(signal), 3000);
  }

  function connectSocket() {
    const address = select("monitor-ws-url").value.trim();
    const previous = socket;
    socket = null;
    previous?.close();
    try {
      const stream = new WebSocket(address);
      socket = stream;
      show("monitor-ws-state", "正在连接…");
      stream.addEventListener("open", () => { if (socket === stream) show("monitor-ws-state", "扩展状态流已连接"); });
      stream.addEventListener("message", (event) => {
        if (socket !== stream) return;
        try {
          const envelope = JSON.parse(event.data);
          const type = envelope.type || envelope.event || envelope.interface;
          const data = envelope.data || envelope.payload || envelope;
          if (type === "detect" || data.detections) {
            auxiliaryDetectionAt = performance.now();
            showDetection(data, "WebSocket 识别");
          } else if (type === "transform" || data.reachable !== undefined) {
            showTransform(data);
            show("motion-source", "WebSocket 坐标");
          } else if (type === "execute_pick" || data.accepted !== undefined || data.duration_ms !== undefined) {
            showExecution(data);
            show("motion-source", "WebSocket 执行");
          } else if (data.robot_state || data.message) {
            show("monitor-ws-state", [data.robot_state, data.message].filter(Boolean).join(" · "));
          }
          message("monitor-error", data.error_code ? "扩展接口告警 · " + data.error_code : "");
        } catch (error) {
          message("monitor-error", "扩展状态消息无法读取 · " + error.message);
        }
      });
      stream.addEventListener("close", () => { if (socket === stream) show("monitor-ws-state", "扩展状态流已断开"); });
      stream.addEventListener("error", () => { if (socket === stream) show("monitor-ws-state", "扩展状态流连接失败"); });
    } catch (error) {
      show("monitor-ws-state", address ? "连接失败 · " + error.message : "请填写 WebSocket 地址。");
    }
  }

  image.addEventListener("error", () => {
    message("camera-error", "图片加载失败。");
    frameKey = undefined;
    if (imageSource === "board" && fallbackUrl) showFallback();
    else {
      if (imageSource === "fallback") fallbackUrl = undefined;
      showImage(null, null);
      show("camera-live-label", "无可用画面");
      show("camera-source", "画面不可用");
      show("camera-caption", "等待可用的相机画面");
      show("camera-timestamp", null);
      show("frame-id", null);
      select("camera-source").classList.remove("live");
    }
  });
  select("connect-monitor").addEventListener("click", connectSocket);
  select("load-monitor-image").addEventListener("click", () => {
    const address = select("monitor-image-url").value.trim();
    try {
      if (!["http:", "https:"].includes(new URL(address, window.location.href).protocol) || !address) {
        throw new Error();
      }
      fallbackUrl = address;
      message("camera-error");
      if (imageSource !== "board" || !online || performance.now() - receivedFrameAt >= 5000) {
        imageSource = null;
        showFallback();
      }
    } catch {
      message("camera-error", "请输入有效的 HTTP 或 HTTPS 图片地址。");
    }
  });

  function start(mode, signal) {
    const previous = socket;
    socket = null;
    previous?.close();
    showImage(null, null);
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = undefined;
    frameKey = undefined;
    auxiliaryDetectionAt = undefined;
    show("monitor-ws-state", "未连接");
    message("camera-error");
    message("monitor-error");
    select("camera-source").classList.remove("live");
    const live = mode === "live";
    select("monitor-connection").hidden = !live;
    show("camera-caption", live ? "等待设备上传相机画面" : "模拟工作台画面");
    show("camera-live-label", live ? "等待画面" : "模拟画面");
    show("camera-source", live ? "等待画面" : "模拟画面");
    show("camera-source-detail", live ? "板端相机" : "模拟工作台");
    show("camera-timestamp", null);
    show("frame-id", null);
    showDetection({}, live ? "等待数据" : "模拟识别");
    showTransform(live ? {} : { x_m: 0.12, y_m: 0.23, z_m: 0.04, reachable: true, calibration_id: "calibration_demo" });
    showExecution({});
    show("motion-source", live ? "等待数据" : "模拟坐标");
    if (live) {
      pollFrame(signal);
    } else {
      placeholder.hidden = true;
      scene.hidden = false;
      renderDemo(signal);
    }
  }

  return {
    start,
    showBoardDetections(detections) {
      if (performance.now() - auxiliaryDetectionAt < 5000) return;
      const results = Array.isArray(detections) ? detections : [];
      showDetection({ detections: results }, results.length ? "板端识别" : "暂无识别");
    },
    setOnline(value) {
      online = value;
      updateCameraLabel();
    },
  };
}
