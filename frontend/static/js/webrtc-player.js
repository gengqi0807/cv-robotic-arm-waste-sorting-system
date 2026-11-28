"use strict";

// Full ICE gathering before POST avoids a trickle-ICE dependency in the player.
class RobotWebRTCPlayer {
  constructor(video, onState) {
    this.video = video;
    this.onState = onState;
    this.active = false;
    this.generation = 0;
  }

  async start(url) {
    this.stop();
    this.url = url;
    this.active = true;
    this.connect();
  }

  stop() {
    this.active = false;
    this.generation += 1;
    clearTimeout(this.retryTimer);
    this.cleanup();
  }

  cleanup() {
    clearTimeout(this.watchdog);
    clearTimeout(this.statsTimer);
    if (this.abort) this.abort.abort();
    if (this.pc) {
      this.pc.ontrack = null;
      this.pc.onconnectionstatechange = null;
      this.pc.close();
      this.pc = null;
    }
    if (this.video.srcObject) {
      this.video.srcObject.getTracks().forEach(track => track.stop());
    }
    this.video.srcObject = null;
    if (this.sessionUrl) {
      fetch(this.sessionUrl, { method: "DELETE", keepalive: true }).catch(() => {});
      this.sessionUrl = null;
    }
  }

  failed(message, generation) {
    if (!this.active || generation !== this.generation) return;
    this.generation += 1;
    this.cleanup();
    this.onState("retrying", message);
    this.retryTimer = setTimeout(() => this.connect(), 3000);
  }

  async connect() {
    if (!this.active) return;
    const generation = ++this.generation;
    if (!window.RTCPeerConnection) {
      this.onState("error", "当前浏览器不支持 WebRTC，请使用新版 Chrome 或 Edge。");
      return;
    }
    const abort = new AbortController();
    this.abort = abort;
    const pc = new RTCPeerConnection({ iceServers: [] });
    this.pc = pc;
    this.onState("connecting", "正在连接实时视频…");
    this.watchdog = setTimeout(() => this.failed("尚未收到视频，正在重连。请检查推流服务和网络。", generation), 15000);
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = event => {
      if (!this.active || generation !== this.generation) return;
      this.video.srcObject = event.streams[0] || new MediaStream([event.track]);
      this.video.play().catch(() => this.onState("error", "浏览器未自动播放，请点击视频播放按钮。"));
    };
    pc.onconnectionstatechange = () => {
      if (["failed", "disconnected"].includes(pc.connectionState)) {
        this.failed("视频连接中断，正在重连…", generation);
      }
    };
    try {
      await pc.setLocalDescription(await pc.createOffer());
      await new Promise((resolve, reject) => {
        if (pc.iceGatheringState === "complete") { resolve(); return; }
        const timer = setTimeout(() => { dispose(); reject(new Error("视频连接协商超时")); }, 5000);
        const onAbort = () => { dispose(); reject(new Error("连接已取消")); };
        const onChange = () => { if (pc.iceGatheringState === "complete") { dispose(); resolve(); } };
        function dispose() {
          clearTimeout(timer);
          pc.removeEventListener("icegatheringstatechange", onChange);
          abort.signal.removeEventListener("abort", onAbort);
        }
        pc.addEventListener("icegatheringstatechange", onChange);
        abort.signal.addEventListener("abort", onAbort, { once: true });
        if (abort.signal.aborted) onAbort();
      });
      if (!this.active || generation !== this.generation) return;
      const response = await fetch(this.url, {
        method: "POST", headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription.sdp, signal: abort.signal,
      });
      if (response.status !== 201) throw new Error(`视频服务未就绪（HTTP ${response.status}）`);
      const location = response.headers.get("Location");
      const sessionUrl = location ? new URL(location, this.url).href : null;
      if (!this.active || generation !== this.generation) {
        if (sessionUrl) fetch(sessionUrl, { method: "DELETE", keepalive: true }).catch(() => {});
        return;
      }
      this.sessionUrl = sessionUrl;
      await pc.setRemoteDescription({ type: "answer", sdp: await response.text() });
      // Confirm decoded frames (a successful SDP POST alone isn't live video).
      let lastFrames = 0;
      const watchFrames = async () => {
        if (!this.active || generation !== this.generation) return;
        try {
          const stats = await pc.getStats();
          let frames = 0;
          stats.forEach(stat => {
            if (stat.type === "inbound-rtp" && stat.kind === "video") frames += stat.framesDecoded || 0;
          });
          if (!this.active || generation !== this.generation) return;
          if (frames > lastFrames) {
            clearTimeout(this.watchdog);
            this.onState("playing", "实时视频已连接");
            this.watchdog = setTimeout(() => this.failed("视频画面停止更新，正在重连…", generation), 8000);
            lastFrames = frames;
          }
          this.statsTimer = setTimeout(watchFrames, 1000);
        } catch (_) { this.failed("视频连接异常，正在重连…", generation); }
      };
      watchFrames();
    } catch (error) {
      this.failed(error.message || "视频连接失败", generation);
    }
  }
}
window.RobotWebRTCPlayer = RobotWebRTCPlayer;
